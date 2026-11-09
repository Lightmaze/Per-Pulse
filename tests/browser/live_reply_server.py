"""Local server for the live-reply browser flow; model replies are a synthetic HTTP fixture.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The fixture streams the first part of a reply, then holds until the browser
flow releases it.
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

os.environ['PER_PULSE_LIVE_QA_KEY'] = 'synthetic-test-credential'
temporary = tempfile.TemporaryDirectory(prefix='per-pulse-live-')
state = Path(temporary.name)
(state / 'workspace').mkdir()
release = threading.Event()


def event(delta, finish=None):
    return ('data: ' + json.dumps({'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}) + '\n\n').encode()


def handler(request):
    messages = json.loads(request.content)['messages']
    latest = next((m['content'] for m in reversed(messages) if m['role'] == 'user'), '')
    def body():
        if latest == 'REFUSAL_FIXTURE':
            yield event({'content': None, 'refusal': 'Synthetic refusal fixture. I cannot complete this request.'})
            yield event({}, 'stop')
            yield b'data: [DONE]\n\n'
            return
        yield event({'content': 'Synthetic fixture. Drafting the plan:\n\n1. Read the notes.'})
        release.wait(30)
        yield event({'content': '\n2. Write the summary.'})
        yield event({}, 'stop')
        yield b'data: [DONE]\n\n'
    return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=body())


def factory(scope):
    return ChatHTTPModelAdapter(
        ChatHTTPConfig('https://fixture.invalid/chat/completions', 'synthetic-live-fixture',
                       credential_env='PER_PULSE_LIVE_QA_KEY'),
        transport=httpx.MockTransport(handler),
    )


app = create_app(state, model_factory=factory)


@app.post('/__live/release')
def release_reply():
    release.set()
    return {'ok': True}


fixture_routes = app.router.routes[-1:]
del app.router.routes[-1:]
frontend_index = next(i for i, route in enumerate(app.router.routes) if getattr(route, 'path', None) == '/{route:path}')
app.router.routes[frontend_index:frontend_index] = fixture_routes

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('PER_PULSE_QA_PORT', '8151')), log_level='warning')
