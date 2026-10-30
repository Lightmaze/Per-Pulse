"""Open actual background answers, preserving their native causal identity.

Explicit TEST_DOUBLE responses; real World/Kernel persistence and admission.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import pytest

from per_pulse.service import PulseSettings
from test_pulse_integration import (
    TransportFixture, assistant_count, client_for, eventually, page, view,
)


@pytest.fixture
def completed_background(tmp_path, monkeypatch):
    fixture = TransportFixture(monkeypatch)
    settings = PulseSettings(enabled=True, spontaneous_rate=1,
                             check_interval_seconds=.01, refractory_seconds=.05,
                             max_background_pulses_per_hour=1)
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        work = page(client, 'Continuing Work', 'A_LOCAL_HISTORY')
        finished = eventually(lambda: view(client, work), lambda v: assistant_count(v) == 2)
        activation = eventually(lambda: client.get('/api/pulse').json()['recentActivations'],
                                lambda items: len(items) == 1 and items[0]['status'] == 'settled')[0]
        result = finished['messages'][-1]['id']
        yield client, fixture, work, activation, result


def test_spontaneous_answer_is_linked_without_inventing_an_activation_message(completed_background):
    client, fixture, work, activation, result = completed_background
    assert activation['messageId'] is None, 'The spontaneous input has no displayable content.'
    assert activation.get('resultMessageId') == result
    runtime = client.app.state.pages.runtime
    assert runtime.causal_ledger.get_event(result).parent_event_id == activation['eventId']
    before = len(fixture.requests)
    anchored = client.get(f'/api/pages/{work}', params={'sourceMessageId': result}).json()
    assert any(message['id'] == result for message in anchored['messages'])
    assert len(fixture.requests) == before, 'Opening a result must not create model work.'
    client.post(f'/api/pages/{work}/messages', json={'content': 'A later foreground answer.'})
    eventually(lambda: view(client, work), lambda v: assistant_count(v) == 3)
    observed = client.get('/api/pulse/observation').json()['pulse']['recentActivations'][0]
    assert observed['resultMessageId'] == result, 'Do not substitute the latest Work message.'
    assert observed['sourceMessageId'] == activation['sourceEventId']


def test_observer_counts_the_verified_answer_without_starting_work(completed_background, tmp_path):
    from per_pulse.pulse_observer import observe
    from test_pulse_observer import Clock, records

    client, fixture, work, activation, result = completed_background
    count = len(fixture.requests)
    clock = Clock()
    output = tmp_path / 'observed.jsonl'
    report = observe('http://127.0.0.1:8000', output,
                     duration_seconds=.1, interval_seconds=.1, client=client,
                     clock=clock, sleep=clock.sleep)
    assert report['observed_navigable_work_results_by_page'] == {work: 1}
    assert report['provider_quality_or_cost_verified'] is False
    changes = [item for row in records(output) if row['type'] == 'sample'
               for item in row['event_updates']]
    assert changes[0]['eventId'] == activation['eventId']
    assert changes[0]['messageId'] is None
    assert changes[0]['resultMessageId'] == result
    assert len(fixture.requests) == count


def test_background_result_link_survives_disconnected_reopen(tmp_path, monkeypatch):
    fixture = TransportFixture(monkeypatch)
    settings = PulseSettings(enabled=True, spontaneous_rate=1,
                             check_interval_seconds=.01, refractory_seconds=.05,
                             max_background_pulses_per_hour=1)
    with client_for(tmp_path, pulse=settings, model_factory=fixture.factory) as client:
        work = page(client, 'Continuing Work', 'A_LOCAL_HISTORY')
        finished = eventually(lambda: view(client, work), lambda v: assistant_count(v) == 2)
        result = finished['messages'][-1]['id']
        before = eventually(lambda: client.get('/api/pulse').json()['recentActivations'],
                            lambda items: len(items) == 1 and items[0].get('resultMessageId') == result)
    count = len(fixture.requests)
    with client_for(tmp_path, pulse=settings, model_factory=None) as client:
        after = client.get('/api/pulse').json()['recentActivations']
        assert after == before
        assert view(client, work)['messages'] == finished['messages']
        assert len(fixture.requests) == count


@pytest.mark.parametrize('fault', ['turn_failed', 'unaccepted', 'wrong_context',
                                  'different_parent', 'different_center', 'unsettled_result',
                                  'not_an_answer', 'empty_answer', 'wrong_result_pointer'])
def test_background_link_rejects_mismatched_native_evidence(completed_background, fault):
    client, _, work, activation, result = completed_background
    runtime = client.app.state.pages.runtime
    # Explicitly malformed persisted specimens, never normal product writes.
    statement, parameters = {
        'turn_failed': ("UPDATE harness_turns SET state='failed' WHERE event_id=?", (activation['eventId'],)),
        'unaccepted': ("UPDATE harness_turns SET prompt_accepted=0 WHERE event_id=?", (activation['eventId'],)),
        'wrong_context': ("UPDATE harness_turns SET execution_context='work:other' WHERE event_id=?", (activation['eventId'],)),
        'different_parent': ("UPDATE causal_events SET parent_event_id=? WHERE id=?", (activation['sourceEventId'], result)),
        'different_center': ("UPDATE causal_events SET center_id=NULL WHERE id=?", (result,)),
        'unsettled_result': ("UPDATE causal_events SET status='uncertain' WHERE id=?", (result,)),
        'not_an_answer': ("UPDATE causal_events SET kind='stimulus', source='user' WHERE id=?", (result,)),
        'empty_answer': ("UPDATE causal_events SET content='' WHERE id=?", (result,)),
        'wrong_result_pointer': ("UPDATE harness_turns SET result_event_id=? WHERE event_id=?", (activation['sourceEventId'], activation['eventId'])),
    }[fault]
    with runtime.storage._lock, runtime.storage._immediate_transaction() as connection:
        connection.execute(statement, parameters)
    # Commit the malformed specimen before a normal scheduler lease check.
    # Otherwise BEGIN IMMEDIATE rejects the open fixture transaction and its
    # rollback can silently erase the fault before the projection observes it.
    runtime._require_runtime_owner()
    current = client.get('/api/pulse').json()['recentActivations'][0]
    assert current.get('resultMessageId') is None
    assert current['eventId'] == activation['eventId']
    assert current['messageId'] is None
