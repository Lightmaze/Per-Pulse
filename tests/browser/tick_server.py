"""Local server for the /tick browser flow; model replies are a synthetic HTTP fixture.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import json
import os
from pathlib import Path
import tempfile
import threading
import time

import httpx
import uvicorn

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

os.environ['PER_PULSE_TICK_QA_KEY'] = 'synthetic-test-credential'
temporary = tempfile.TemporaryDirectory(prefix='per-pulse-tick-')
state = Path(temporary.name)
lock = threading.Lock()
requests: list[list[dict]] = []
delay = {'seconds': 0.0}


def handler(request):
    body = json.loads(request.content)
    time.sleep(delay['seconds'])
    with lock:
        requests.append(body['messages'])
        output = f'Synthetic fixture step {len(requests)}.'
    chunks = [
        {'choices': [{'index': 0, 'delta': {'content': output}, 'finish_reason': None}]},
        {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
        {'choices': [], 'usage': {'prompt_tokens': 8, 'completion_tokens': 4, 'total_tokens': 12}},
    ]
    stream = ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
    return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=stream)


def factory(scope):
    return ChatHTTPModelAdapter(
        ChatHTTPConfig('https://fixture.invalid/chat/completions', 'synthetic-tick-fixture',
                       credential_env='PER_PULSE_TICK_QA_KEY'),
        transport=httpx.MockTransport(handler),
    )


app = create_app(state, model_factory=factory)


@app.get('/__tick/requests')
def recorded():
    with lock:
        return {'requests': requests}


@app.post('/__tick/delay')
def slow(body: dict):
    delay['seconds'] = float(body.get('seconds', 0))
    return delay


fixture_routes = app.router.routes[-2:]
del app.router.routes[-2:]
frontend_index = next(i for i, route in enumerate(app.router.routes) if getattr(route, 'path', None) == '/{route:path}')
app.router.routes[frontend_index:frontend_index] = fixture_routes

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('PER_PULSE_QA_PORT', '8147')), log_level='warning')
