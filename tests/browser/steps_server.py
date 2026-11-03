"""Local server for the live-steps browser flow; model replies are a synthetic HTTP fixture.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import json
import os
from pathlib import Path
import tempfile
import threading

import httpx
import uvicorn

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

os.environ['PER_PULSE_STEPS_QA_KEY'] = 'synthetic-test-credential'
temporary = tempfile.TemporaryDirectory(prefix='per-pulse-steps-')
state = Path(temporary.name)
(state / 'workspace').mkdir()
(state / 'workspace' / 'notes.md').write_text('# Notes\n\nA plan to review.\n', encoding='utf-8')
(state / 'workspace' / 'plan.md').write_text('# Plan\n\nShip on Friday.\n', encoding='utf-8')
release = threading.Event()


def handler(request):
    messages = json.loads(request.content)['messages']
    tools = sum(1 for m in messages if m.get('role') == 'tool')
    if tools < 2:
        path = 'notes.md' if tools == 0 else 'plan.md'
        delta = {'tool_calls': [{'index': 0, 'id': f'steps-read-{tools}', 'type': 'function', 'function': {
            'name': 'read_file', 'arguments': json.dumps({'path': path})}}]}
        finish = 'tool_calls'
    else:
        release.wait(30)
        delta, finish = {'content': 'Synthetic fixture: both files are read.'}, 'stop'
    events = [{'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
              {'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]}]
    stream = ''.join('data: ' + json.dumps(e) + '\n\n' for e in events) + 'data: [DONE]\n\n'
    return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=stream)


def factory(scope):
    return ChatHTTPModelAdapter(
        ChatHTTPConfig('https://fixture.invalid/chat/completions', 'synthetic-steps-fixture',
                       credential_env='PER_PULSE_STEPS_QA_KEY'),
        transport=httpx.MockTransport(handler),
    )


app = create_app(state, model_factory=factory)


@app.post('/__steps/release')
def release_turn():
    release.set()
    return {'ok': True}


fixture_routes = app.router.routes[-1:]
del app.router.routes[-1:]
frontend_index = next(i for i, route in enumerate(app.router.routes) if getattr(route, 'path', None) == '/{route:path}')
app.router.routes[frontend_index:frontend_index] = fixture_routes

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('PER_PULSE_QA_PORT', '8150')), log_level='warning')
