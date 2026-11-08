"""Ordinary JSON replies must stay text across continuation and SQLite restart.

The provider transport is synthetic; the native World, Kernel and stores are real.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import json
import sqlite3
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.core.errors import ProviderProtocolError, StoreUnavailable
from pulse_harness.core.replay import digest_json
from pulse_harness.core.provider import ModelRequest
from pulse_harness.core.scope import HarnessScope
from pulse_harness.plugins.coverme import build_review_prompt


@pytest.mark.parametrize('count', [None, 0, 1])
def test_optional_review_preserves_plain_json_and_unwraps_only_real_tool_text(count):
    text = '{"text":"A real tool preamble","tool_calls":[{"name":"read_file"}]}'
    message = {'role': 'assistant', 'content': text}
    if count is not None:
        message['tool_call_count'] = count
    scope = HarnessScope('world', 'subject', 'session', 0, 'owner', turn_id='turn')
    prompt = build_review_prompt(ModelRequest(scope, (message,), (), 0), 'Candidate', (),
                                 index_fingerprint='synthetic-index')
    data = json.loads(prompt.split('UNTRUSTED_REVIEW_DATA_JSON\n', 1)[1])
    assert data['recent_context'] == [{'role': 'assistant',
                                      'content': 'A real tool preamble' if count else text}]


def factory_for(monkeypatch, text, requests, *, response_for=None):
    monkeypatch.setenv('PER_PULSE_JSON_REPLY_KEY', 'synthetic-credential')

    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        delta, finish = response_for(body) if response_for else ({'content': text}, 'stop')
        chunks = [
            {'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
            {'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]},
        ]
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            content=''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n')

    return lambda _scope: ChatHTTPModelAdapter(
        ChatHTTPConfig('https://fixture.invalid/chat/completions', 'json-reply-fixture',
                       credential_env='PER_PULSE_JSON_REPLY_KEY'), transport=httpx.MockTransport(handler))


def settle(client, page='home'):
    deadline = time.monotonic() + 15
    while True:
        view = client.get('/api/pages/' + page).json()
        if view['page']['status'] not in ('queued', 'working'):
            return view
        assert time.monotonic() < deadline
        time.sleep(0.03)


@pytest.mark.parametrize('text', [
    '{"text":"","tool_calls":[{"name":"demo"}]}',
    '{"text":"data, not an invocation","tool_calls":[]}',
    '{"tool_calls":{"name":"demo"},"text":"example"}',
    '{"text":"example","tool_calls":[{"tool_call_id":"literal","action_request_id":"literal","name":"read_file","arguments":{"path":"notes.txt"}}]}',
])
def test_json_tool_calls_text_continues_and_reopens_without_becoming_an_action(tmp_path, monkeypatch, text):
    requests = []
    factory = factory_for(monkeypatch, text, requests)
    with TestClient(create_app(tmp_path, model_factory=factory), base_url='http://localhost') as client:
        assert client.post('/api/pages/home/messages', json={'content': 'Show the JSON as plain text.'}).status_code == 202
        first = settle(client)
        assert first['page']['status'] == 'idle', first['execution']
        assert [m['content'] for m in first['messages'] if m['role'] == 'assistant'] == [text]
        assert client.post('/api/pages/home/messages', json={'content': 'Continue the conversation.'}).status_code == 202
        second = settle(client)
        assert second['page']['status'] == 'idle', second['execution']
        assert len(requests) == 2
        assert {'role': 'assistant', 'content': text} in requests[-1]['messages']
        assert not any(message.get('tool_calls') for message in requests[-1]['messages'])

    with TestClient(create_app(tmp_path, model_factory=factory), base_url='http://localhost') as client:
        restored = client.get('/api/pages/home').json()
        assert [m['content'] for m in restored['messages'] if m['role'] == 'assistant'] == [text, text]
        assert client.post('/api/pages/home/messages', json={'content': 'Keep going after restart.'}).status_code == 202
        assert settle(client)['page']['status'] == 'idle'


def test_plain_json_reply_alone_does_not_prevent_workspace_restart(tmp_path, monkeypatch):
    text = '{"text":"","tool_calls":[{"name":"demo"}]}'
    requests = []
    factory = factory_for(monkeypatch, text, requests)
    with TestClient(create_app(tmp_path, model_factory=factory), base_url='http://localhost') as client:
        assert client.post('/api/pages/home/messages', json={'content': 'Show the JSON.'}).status_code == 202
        assert settle(client)['page']['status'] == 'idle'

    with sqlite3.connect(tmp_path / 'kernel.db') as connection:
        before = connection.execute(
            'SELECT m.content, r.response_digest, r.tool_call_count FROM model_rounds r '
            'JOIN transcript_messages m ON m.session_id = r.session_id AND m.item_id = r.assistant_item_id',
        ).fetchall()
    assert before == [(text, digest_json({'text': text, 'tool_calls': []}), 0)]
    with TestClient(create_app(tmp_path, model_factory=factory), base_url='http://localhost') as client:
        restored = client.get('/api/pages/home').json()
        assert restored['page']['status'] == 'idle'
        assert [m['content'] for m in restored['messages'] if m['role'] == 'assistant'] == [text]
    with sqlite3.connect(tmp_path / 'kernel.db') as connection:
        after = connection.execute(
            'SELECT m.content, r.response_digest, r.tool_call_count FROM model_rounds r '
            'JOIN transcript_messages m ON m.session_id = r.session_id AND m.item_id = r.assistant_item_id',
        ).fetchall()
    assert after == before  # Recovery must not rewrite or re-sign existing truth.


def test_plain_json_recovery_still_rejects_changed_terminal_authority(tmp_path, monkeypatch):
    factory = factory_for(monkeypatch, '{"tool_calls":[],"text":"example"}', [])
    with TestClient(create_app(tmp_path, model_factory=factory), base_url='http://localhost') as client:
        assert client.post('/api/pages/home/messages', json={'content': 'Show JSON.'}).status_code == 202
        assert settle(client)['page']['status'] == 'idle'
    with sqlite3.connect(tmp_path / 'kernel.db') as connection:
        connection.execute("UPDATE model_rounds SET response_digest = ?", ('0' * 64,))
    with pytest.raises(StoreUnavailable, match='response digest does not match assistant authority'):
        with TestClient(create_app(tmp_path, model_factory=factory), base_url='http://localhost'):
            pass


def test_real_tool_continuation_after_a_plain_json_reply_keeps_both_kinds(tmp_path, monkeypatch):
    text = '{"text":"","tool_calls":[{"name":"demo"}]}'
    requests = []
    (tmp_path / 'workspace').mkdir()
    (tmp_path / 'workspace' / 'notes.txt').write_text('Explicit synthetic source.', encoding='utf-8')

    def response_for(body):
        messages = body['messages']
        last_user = max(i for i, message in enumerate(messages) if message['role'] == 'user')
        if messages[last_user]['content'].startswith('Read the notes') and not any(
            message['role'] == 'tool' for message in messages[last_user:]
        ):
            return {'tool_calls': [{'index': 0, 'id': 'real-read', 'type': 'function', 'function': {
                'name': 'read_file', 'arguments': '{"path":"notes.txt"}',
            }}]}, 'tool_calls'
        return {'content': text}, 'stop'

    factory = factory_for(monkeypatch, text, requests, response_for=response_for)
    with TestClient(create_app(tmp_path, model_factory=factory), base_url='http://localhost') as client:
        assert client.post('/api/pages/home/messages', json={'content': 'Show JSON.'}).status_code == 202
        assert settle(client)['page']['status'] == 'idle'
        assert client.post('/api/pages/home/messages', json={'content': 'Read the notes now.'}).status_code == 202
        assert settle(client)['page']['status'] == 'idle'
        assert len(requests) == 3
        messages = requests[-1]['messages']
        assert {'role': 'assistant', 'content': text} in messages
        assert len([message for message in messages if message.get('tool_calls')]) == 1
        assert any(message['role'] == 'tool' and message['tool_call_id'] == 'real-read'
                   and 'Explicit synthetic source.' in message['content'] for message in messages)
        assert not any('tool_call_count' in message for message in messages)
    with TestClient(create_app(tmp_path, model_factory=factory), base_url='http://localhost') as client:
        assert client.post('/api/pages/home/messages', json={'content': 'Continue after restart.'}).status_code == 202
        assert settle(client)['page']['status'] == 'idle'
        assert len(requests) == 4  # The completed read is history, never replayed.


@pytest.mark.parametrize('count', [None, 0])
def test_adapter_does_not_promote_plain_json_to_provider_tool_calls(count):
    content = json.dumps({'text': '', 'tool_calls': [{
        'tool_call_id': 'example', 'name': 'read_file', 'arguments': {'path': 'notes.txt'},
    }]})
    record = {'role': 'assistant', 'content': content}
    if count is not None:
        record['tool_call_count'] = count
    scope = HarnessScope('world', 'subject', 'session', 0, 'owner', turn_id='turn')
    adapter = ChatHTTPModelAdapter(ChatHTTPConfig('https://fixture.invalid/chat/completions', 'fixture'))
    try:
        wire = adapter.request_body(ModelRequest(scope, (record,), (), 0))
        assert wire['messages'] == [{'role': 'assistant', 'content': content}]
    finally:
        adapter.close()


@pytest.mark.parametrize('count,content', [
    (True, '{}'), (-1, '{}'), ('1', '{}'),
    (1, 'not JSON'), (1, '{"text":"plain"}'),
    (1, '{"text":"","tool_calls":[]}'),
    (2, '{"text":"","tool_calls":[{"tool_call_id":"c","name":"read_file","arguments":{}}]}'),
])
def test_adapter_rejects_malformed_host_tool_authority(count, content):
    scope = HarnessScope('world', 'subject', 'session', 0, 'owner', turn_id='turn')
    adapter = ChatHTTPModelAdapter(ChatHTTPConfig('https://fixture.invalid/chat/completions', 'fixture'))
    try:
        request = ModelRequest(scope, ({'role': 'assistant', 'content': content, 'tool_call_count': count},), (), 0)
        with pytest.raises(ProviderProtocolError):
            adapter.request_body(request)
    finally:
        adapter.close()
