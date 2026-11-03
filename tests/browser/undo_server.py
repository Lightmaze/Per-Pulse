"""Local server for the file-change Undo browser flow; model replies are a synthetic HTTP fixture.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile

import httpx
import uvicorn

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

os.environ['PER_PULSE_UNDO_QA_KEY'] = 'synthetic-test-credential'
temporary = tempfile.TemporaryDirectory(prefix='per-pulse-undo-')
state = Path(temporary.name)
original = '# Launch plan\n\nShip on Friday.\n'
revised = '# Launch plan\n\nShip on Monday after the review.\n'
(state / 'workspace').mkdir()
(state / 'workspace' / 'plan.md').write_text(original, encoding='utf-8')


def handler(request):
    messages = json.loads(request.content)['messages']
    last_user = max((i for i, m in enumerate(messages) if m.get('role') == 'user'), default=0)
    current = messages[last_user:]
    text = json.dumps(current)
    if not any(m.get('role') == 'tool' for m in current) and ('REVISE_PLAN' in text or 'WRITE_SUMMARY' in text):
        arguments = (
            {'path': 'plan.md', 'expected_digest': hashlib.sha256(original.encode()).hexdigest(), 'content': revised}
            if 'REVISE_PLAN' in text else
            {'path': 'summary.md', 'expected_digest': None, 'content': 'A short summary.\n'}
        )
        delta = {'tool_calls': [{'index': 0, 'id': 'fixture-edit', 'type': 'function', 'function': {
            'name': 'mutate_file', 'arguments': json.dumps(arguments)}}]}
        finish = 'tool_calls'
    else:
        delta, finish = {'content': 'Synthetic fixture: the file is updated.'}, 'stop'
    chunks = [
        {'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
        {'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]},
    ]
    stream = ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
    return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=stream)


def factory(scope):
    return ChatHTTPModelAdapter(
        ChatHTTPConfig('https://fixture.invalid/chat/completions', 'synthetic-undo-fixture',
                       credential_env='PER_PULSE_UNDO_QA_KEY'),
        transport=httpx.MockTransport(handler),
    )


# The same options the per-pulse command uses by default.
app = create_app(state, model_factory=factory, allow_workspace_actions=True, auto_apply_edits=True)


@app.get('/__undo/files')
def files():
    workspace = state / 'workspace'
    return {name: (workspace / name).read_text(encoding='utf-8') if (workspace / name).exists() else None
            for name in ('plan.md', 'summary.md')}


@app.post('/__undo/edit-summary')
def edit_summary():
    (state / 'workspace' / 'summary.md').write_text('Edited by hand.\n', encoding='utf-8')
    return {'ok': True}


fixture_routes = app.router.routes[-2:]
del app.router.routes[-2:]
frontend_index = next(i for i, route in enumerate(app.router.routes) if getattr(route, 'path', None) == '/{route:path}')
app.router.routes[frontend_index:frontend_index] = fixture_routes

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('PER_PULSE_QA_PORT', '8148')), log_level='warning')
