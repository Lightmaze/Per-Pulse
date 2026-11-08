"""Plain JSON browser fixture: synthetic HTTP replies, real World/Kernel/SQLite.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import asyncio
import json
import os
import sqlite3
from uuid import uuid4
from pathlib import Path

import httpx
import uvicorn
from per_pulse.app import create_app

from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

TEXT = '{"text":"","tool_calls":[{"name":"demo"}]}'
os.environ['PER_PULSE_JSON_BROWSER_KEY'] = 'synthetic-test-credential'
requests = []
generation = uuid4().hex


def handler(request):
    body = json.loads(request.content)
    requests.append(body)
    # Ordinary assistant JSON must never become provider-native tool calls.
    for message in body['messages']:
        assert not message.get('tool_calls'), message
        assert 'tool_call_count' not in message, message
    chunks = [
        {'choices': [{'index': 0, 'delta': {'content': TEXT}, 'finish_reason': None}]},
        {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
    ]
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
        content=''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n')


def factory(_scope):
    return ChatHTTPModelAdapter(ChatHTTPConfig('https://fixture.invalid/chat/completions',
        'synthetic-plain-json-browser-fixture', credential_env='PER_PULSE_JSON_BROWSER_KEY'),
        transport=httpx.MockTransport(handler))


app = create_app(Path(os.environ['PER_PULSE_QA_DATA_DIR']), model_factory=factory,
    web_dir=os.environ.get('PER_PULSE_QA_WEB_DIR'))


@app.post('/__fixture/plain-json/stop')
async def stop():
    # Await the native shutdown flight while the event loop can still drain.
    # A page's idle projection alone does not prove the runtime lease released.
    runtime = app.state.pages.runtime
    await runtime.stop(timeout=15)
    report = await asyncio.to_thread(runtime.close)
    with sqlite3.connect(Path(os.environ['PER_PULSE_QA_DATA_DIR']) / 'world.db') as db:
        state = db.execute('SELECT state FROM runtime_leases').fetchone()[0]
    print('Fixture shutdown:', report.clean, report.owner_lease, state, generation, flush=True)
    assert state == 'released'
    server.should_exit = True
    return {'stopping': True}


@app.get('/__fixture/plain-json/status')
def status():
    return {'requests': requests, 'reply': TEXT, 'pid': os.getpid(), 'generation': generation}


fixture_routes = [app.router.routes.pop(), app.router.routes.pop()]
frontend_index = next(i for i, route in enumerate(app.router.routes) if getattr(route, 'path', None) == '/{route:path}')
for fixture_route in fixture_routes:
    app.router.routes.insert(frontend_index, fixture_route)

if __name__ == '__main__':
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1',
        port=int(os.environ['PER_PULSE_QA_PORT']), log_level='warning'))
    server.run()
