"""Diagnostic write failures stay bounded and recover without changing business state.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import json
from pathlib import Path

from pulse_system.core.runtime.publication import RuntimePublicationGate
from pulse_system.interaction.metrics.recorder import MetricsRecorder


def test_home_message_is_saved_while_diagnostic_file_writes_fail(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from per_pulse.app import create_app

    original = Path.open
    with TestClient(create_app(tmp_path, model_factory=None), base_url='http://localhost') as client:
        def unavailable_metrics(self, *args, **kwargs):
            if self.name == 'metrics.jsonl':
                raise OSError('synthetic unavailable diagnostics')
            return original(self, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(Path, 'open', unavailable_metrics)
            recorder = client.app.state.pages.runtime.metrics
            for index in range(20):
                recorder.record('synthetic_disk_failure', index=index)
            response = client.post('/api/pages/home/messages', json={'content': 'Keep the original Home message.'})
            assert response.status_code == 202
            view = client.get('/api/pages/home').json()
            assert any(row['content'] == 'Keep the original Home message.' for row in view['messages'])
            assert recorder.summary()['persistence']['pending_events'] > 0
        recorder.flush()
        assert recorder.summary()['persistence']['pending_events'] == 0


def test_failed_disk_keeps_recent_bounded_diagnostics_and_recovers(tmp_path, monkeypatch):
    path = tmp_path / 'metrics.jsonl'
    recorder = MetricsRecorder(path, memory_cap=3, flush_every=1)
    original = Path.open
    attempts = []

    def unavailable(self, *args, **kwargs):
        attempts.append(self)
        raise OSError('synthetic unavailable disk')

    monkeypatch.setattr('pulse_system.interaction.metrics.recorder.time.monotonic', lambda: 0)
    monkeypatch.setattr(Path, 'open', unavailable)
    for index in range(20):
        recorder.record('sample', index=index)
    assert len(attempts) == 1  # New events do not retry a failing disk in a loop.
    assert [event['index'] for event in recorder.events()] == [17, 18, 19]
    status = recorder.summary()
    assert status['event_counts'] == {'sample': 20}
    assert status['persistence']['pending_events'] == 3
    assert status['persistence']['dropped_events'] == 17
    assert status['persistence']['last_error'] == 'OSError'

    monkeypatch.setattr(Path, 'open', original)
    recorder.flush()  # An explicit flush may retry immediately after repair.
    assert [json.loads(line)['index'] for line in path.read_text().splitlines()] == [17, 18, 19]
    assert recorder.summary()['persistence'] == {
        'pending_events': 0, 'pending_bytes': 0, 'dropped_events': 17,
        'last_error': None, 'revoked': False,
    }


def test_pending_byte_budget_and_oversized_event_are_visible(tmp_path):
    recorder = MetricsRecorder(tmp_path / 'metrics.jsonl', memory_cap=100,
                               flush_every=100, pending_bytes=300)
    for index in range(8):
        recorder.record('sample', index=index, text='\u6587' * 20)
    before = recorder.summary()['persistence']
    assert 0 < before['pending_bytes'] <= 300
    assert before['pending_events'] < 8
    assert before['dropped_events'] == 8 - before['pending_events']
    recorder.record('oversized', text='x' * 400)
    after = recorder.summary()['persistence']
    assert after['pending_bytes'] == before['pending_bytes']
    assert after['dropped_events'] == before['dropped_events'] + 1
    assert recorder.events('oversized')  # It remains a recent observation.
    recorder.flush()
    assert recorder.summary()['persistence']['pending_bytes'] == 0


def test_automatic_retries_back_off_and_success_resets_them(tmp_path, monkeypatch):
    path = tmp_path / 'metrics.jsonl'
    recorder = MetricsRecorder(path, flush_every=1)
    now, attempts = [0.0], []
    original = Path.open

    def unavailable(self, *args, **kwargs):
        attempts.append(now[0])
        raise OSError('synthetic write failure')

    monkeypatch.setattr('pulse_system.interaction.metrics.recorder.time.monotonic', lambda: now[0])
    monkeypatch.setattr(Path, 'open', unavailable)
    for value in [0, 0.5, 1, 2, 3, 6, 7]:
        now[0] = value
        recorder.record('sample', clock=value)
    assert attempts == [0, 1, 3, 7]
    monkeypatch.setattr(Path, 'open', original)
    recorder.flush()
    recorder.record('recovered')
    assert recorder.summary()['persistence']['pending_events'] == 0
    assert json.loads(path.read_text().splitlines()[-1])['type'] == 'recovered'


def test_publication_revocation_stops_durable_queueing(tmp_path):
    gate = RuntimePublicationGate('synthetic-runtime', 1)
    path = tmp_path / 'metrics.jsonl'
    recorder = MetricsRecorder(path, memory_cap=3, flush_every=1,
                               publication_permit=gate.publication_permit)
    recorder.record('before')
    original_bytes = path.read_bytes()
    gate.revoke(reason='test_shutdown')
    for _ in range(20):
        recorder.record('after')
    recorder.flush()
    assert path.read_bytes() == original_bytes
    status = recorder.summary()['persistence']
    assert status['revoked'] and status['pending_events'] == status['pending_bytes'] == 0
    assert status['dropped_events'] == 20
    assert len(recorder.events()) == 3


def test_recovery_preserves_finite_rotation(tmp_path, monkeypatch):
    path = tmp_path / 'metrics.jsonl'
    recorder = MetricsRecorder(path, memory_cap=10, flush_every=1, max_bytes=200, archive_count=2)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'open', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('synthetic')))
        for index in range(20):
            recorder.record('sample', index=index)
    recorder.flush()
    files = sorted(tmp_path.glob('metrics.jsonl*'))
    assert 1 <= len(files) <= 3
    assert all(file.stat().st_size <= 200 for file in files)
    events = [json.loads(line) for file in files for line in file.read_text().splitlines()]
    assert any(event['index'] == 19 for event in events)
    assert recorder.summary()['persistence']['pending_events'] == 0
