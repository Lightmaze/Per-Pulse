"""Local server for the failed-turn browser flow; model replies are a synthetic HTTP fixture.

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

os.environ['PER_PULSE_FAILURE_QA_KEY'] = 'synthetic-test-credential'
temporary = tempfile.TemporaryDirectory(prefix='per-pulse-failure-')
state = Path(temporary.name)
lock = threading.Lock()
failures = {'remaining': 1}


def handler(request):
    with lock:
        fail = failures['remaining'] > 0
        if fail:
            failures['remaining'] -= 1
    if fail:
        return httpx.Response(503, json={'error': {'message': 'Synthetic: service unavailable.'}})
    events = [{'choices': [{'index': 0, 'delta': {'content': 'Synthetic fixture: answered.'}, 'finish_reason': None}]},
              {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}]
    stream = ''.join('data: ' + json.dumps(e) + '\n\n' for e in events) + 'data: [DONE]\n\n'
    return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=stream)


def factory(scope):
    return ChatHTTPModelAdapter(
        ChatHTTPConfig('https://fixture.invalid/chat/completions', 'synthetic-failure-fixture',
                       credential_env='PER_PULSE_FAILURE_QA_KEY'),
        transport=httpx.MockTransport(handler),
    )


app = create_app(state, model_factory=factory)


@app.post('/__failure/next')
def fail_next():
    with lock:
        failures['remaining'] = 1
    return {'ok': True}


fixture_routes = app.router.routes[-1:]
del app.router.routes[-1:]
frontend_index = next(i for i, route in enumerate(app.router.routes) if getattr(route, 'path', None) == '/{route:path}')
app.router.routes[frontend_index:frontend_index] = fixture_routes

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('PER_PULSE_QA_PORT', '8149')), log_level='warning')
