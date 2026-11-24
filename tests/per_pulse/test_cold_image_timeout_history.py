"""Keep actual native deadline history without replaying uncertain effects.

Model responses are explicit TEST_DOUBLEs; timers, cancellation, approvals,
file effects, SQLite, shutdown and Image copies are real.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
import sqlite3
import threading
import time

import pytest

from per_pulse.execution import PageExecution
from pulse_harness.adapters.workspace import StructuredWorkspaceToolProvider
from pulse_system.product import image
from test_cold_image_recovered_history import _rows
from test_execution import _factory, _new_page, _wait
from test_pages import client_for, wait_for
from test_reading_continuity import ReadingFixture, read

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='cold Image requires POSIX')
SOURCE = 'First 🌍 passage.\r\nNext source line.\r\n'


@pytest.fixture
def timed_out_home(tmp_path, monkeypatch, request):
    mode = getattr(request, 'param', 'model')
    home = tmp_path / 'original'
    requests, entered, release = [], threading.Event(), threading.Event()
    options = {}
    if mode in {'model', 'read', 'write'}:
        options.update(entered=entered, release=release)
    if mode == 'read':
        options['tool'] = ('reading', {'op': 'open', 'path': 'source.txt', 'max_chars': 9})
    if mode == 'unknown_write':
        replace = StructuredWorkspaceToolProvider._replace

        def hold_after_effect(provider, plan):
            replace(provider, plan)
            entered.set()
            assert release.wait(8), 'Release the real file-effect boundary.'

        monkeypatch.setattr(StructuredWorkspaceToolProvider, '_replace', hold_after_effect)
    factory = _factory(monkeypatch, requests, **options)
    with client_for(home, model_factory=factory, allow_workspace_actions=True) as client:
        runtime = client.app.state.pages.runtime
        # Approval and tool scheduling on shared CI runners can exceed one
        # second. Reach the real effect boundary before the native deadline;
        # keep the deadline active, and still assert its actual cancellation.
        runtime._mgr._harness_turn_timeout_sec = 5.0
        (home / 'workspace' / 'source.txt').write_bytes(SOURCE.encode())
        execution = PageExecution(runtime)
        instruction = ('HOLD_PROVIDER' if mode == 'model' else
                       'HOLD_AFTER_APPROVAL WRITE_ONE' if mode in {'read', 'write'} else 'WRITE_ONE')
        page = _new_page(client, instruction)
        future = None
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                first = _wait(execution, page, lambda view: bool(view['turnId']))
                turn = first['turnId']
                if mode in {'approval', 'write', 'unknown_write'}:
                    # An action can be published before the turn enters
                    # WAITING_APPROVAL. Follow the same readiness signal as
                    # the UI before asking the native guard to resolve it.
                    pending = _wait(execution, page, lambda view: bool(view['pendingActions'])
                                    and view['pendingActions'][0]['canResolve'])
                    if mode != 'approval':
                        future = pool.submit(execution.resolve_approval, page,
                                             pending['pendingActions'][0]['actionId'], 'allow_once')
                if mode != 'approval':
                    if not entered.wait(12):
                        # Preserve an early approval failure instead of
                        # hiding it behind the later boundary wait.
                        if future is not None and future.done():
                            future.result()
                        pytest.fail('The planned physical boundary was not reached.')
                deadline = time.monotonic() + 14
                while _rows(home, 'kernel.db', 'SELECT status FROM turns WHERE turn_id=?', (turn,)) != [('cancelled',)]:
                    assert time.monotonic() < deadline, 'The actual native deadline did not cancel its turn.'
                    time.sleep(.025)
            finally:
                release.set()
            if future is not None:
                future.result(timeout=8)
        settled = wait_for(client, page, lambda view: view['messages'][0]['status'] == 'uncertain')
        # Approval waiting has a known cancelled action and no uncertain model
        # round. The World timeout is still retained; the execution indicator
        # intentionally describes narrower model/action uncertainty.
        assert settled['execution']['historicalUncertainty'] == (mode != 'approval')
        assert _rows(home, 'world.db', 'SELECT error_code,error_phase,prompt_accepted FROM harness_turns WHERE id=?',
                     (turn,)) == [('world_turn_timeout', 'kernel', 1)]
    assert _rows(home, 'world.db', "SELECT state FROM runtime_leases WHERE scope='pulse_world'") == [('released',)]
    return home, page, turn, mode, requests


@pytest.mark.parametrize('timed_out_home', ['model', 'approval', 'read', 'write'], indirect=True)
def test_explicit_image_retains_timeout_and_completed_work_without_replay(timed_out_home, tmp_path, monkeypatch):
    home, page, turn, mode, requests = timed_out_home
    before_turn = _rows(home, 'world.db', 'SELECT * FROM harness_turns WHERE id=?', (turn,))
    before_event = _rows(home, 'world.db', 'SELECT * FROM causal_events WHERE id=(SELECT event_id FROM harness_turns WHERE id=?)', (turn,))
    before_actions = _rows(home, 'kernel.db', 'SELECT * FROM actions ORDER BY action_request_id')
    archive, copied = tmp_path / 'private.zip', tmp_path / 'restored'
    with pytest.raises(image.ImageError, match='unsettled native turns'):
        image.save(home, archive)
    assert not archive.exists()
    saved = image.save(home, archive, include_recovered_history=True)
    assert saved['retained_uncertain_turns'] == [turn]
    assert image.inspect(archive)['retained_uncertain_turns'] == [turn]
    assert image.restore(archive, copied)['retained_uncertain_turns'] == [turn]
    for directory in (home, copied):
        assert _rows(directory, 'world.db', 'SELECT * FROM harness_turns WHERE id=?', (turn,)) == before_turn
        assert _rows(directory, 'world.db', 'SELECT * FROM causal_events WHERE id=(SELECT event_id FROM harness_turns WHERE id=?)', (turn,)) == before_event
        assert _rows(directory, 'kernel.db', 'SELECT * FROM actions ORDER BY action_request_id') == before_actions
        assert (directory / 'workspace' / 'source.txt').read_bytes() == SOURCE.encode()
        assert (directory / 'workspace' / 'one.txt').exists() == (mode == 'write')
    calls = len(requests)
    with client_for(copied, model_factory=None) as client:
        view = client.get(f'/api/pages/{page}').json()
        assert view['messages'][0]['status'] == 'uncertain'
        assert not view['execution']['pendingActions']
        assert not view['execution']['recoveryRequired']
    assert len(requests) == calls
    if mode == 'read':
        receipt = next(json.loads(m['content'])['output'] for body in requests for m in body['messages']
                       if m['role'] == 'tool')['receipt_id']
        fixture = ReadingFixture(monkeypatch.setenv)
        with client_for(copied, model_factory=fixture.factory) as client:
            resumed = read(client, page, fixture, op='resume', receipt_id=receipt, max_chars=11)['output']
            assert resumed['content'] == SOURCE[9:20]
            assert resumed['resumed_from']['receipt_id'] == receipt


@pytest.mark.parametrize('timed_out_home', ['unknown_write'], indirect=True)
def test_timeout_does_not_make_an_unknown_file_effect_portable(timed_out_home, tmp_path):
    home, _, turn, _, _ = timed_out_home
    assert (home / 'workspace' / 'one.txt').read_bytes() == b'Written by one.txt.\n'
    assert _rows(home, 'kernel.db', 'SELECT status FROM actions WHERE turn_id=?', (turn,)) == [('uncertain',)]
    with pytest.raises(image.ImageError, match='unfinished actions'):
        image.save(home, tmp_path / 'refused.zip', include_recovered_history=True)
    assert not (tmp_path / 'refused.zip').exists()


@pytest.mark.parametrize('fault', ['wrong_phase', 'unaccepted', 'failed_kernel', 'foreign_context', 'unsettled_event'])
def test_timeout_opt_in_requires_exact_native_settlement(timed_out_home, tmp_path, fault):
    home, _, turn, _, _ = timed_out_home
    database, statement = {
        'wrong_phase': ('world.db', "UPDATE harness_turns SET error_phase='transport' WHERE id=?"),
        'unaccepted': ('world.db', 'UPDATE harness_turns SET prompt_accepted=0 WHERE id=?'),
        'failed_kernel': ('kernel.db', "UPDATE turns SET status='failed' WHERE turn_id=?"),
        'foreign_context': ('kernel.db', "UPDATE turns SET context_id='work:other' WHERE turn_id=?"),
        'unsettled_event': ('world.db', 'UPDATE causal_events SET settled_at=NULL WHERE id=(SELECT event_id FROM harness_turns WHERE id=?)'),
    }[fault]
    # Deliberately malformed source candidates; the normal API never makes
    # these edits or repairs them by changing uncertainty into success.
    with sqlite3.connect(home / database) as db:
        db.execute(statement, (turn,))
    with pytest.raises(image.ImageError, match='settled process recovery|matching cancelled Kernel'):
        image.save(home, tmp_path / 'refused.zip', include_recovered_history=True)
    assert not (tmp_path / 'refused.zip').exists()
