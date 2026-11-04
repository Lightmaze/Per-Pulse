"""Local server for the page usage browser flow; model replies are a synthetic HTTP fixture.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The fixture answers each call and reports synthetic usage in a final
usage-only chunk, as an OpenAI-compatible service does.
"""
import json
import os
from pathlib import Path
import tempfile

import httpx
import uvicorn

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

os.environ['PER_PULSE_USAGE_QA_KEY'] = 'synthetic-test-credential'
temporary = tempfile.TemporaryDirectory(prefix='per-pulse-usage-')
state = Path(temporary.name)
(state / 'workspace').mkdir()


def event(delta, finish=None):
    return ('data: ' + json.dumps({'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}) + '\n\n').encode()


def handler(request):
    body = (event({'content': 'Synthetic fixture. The plan has two steps.'})
            + event({}, 'stop')
            + ('data: ' + json.dumps({'choices': [], 'usage': {
                'prompt_tokens': 1200, 'completion_tokens': 300, 'total_tokens': 1500,
                'prompt_tokens_details': {'cached_tokens': 1000}}}) + '\n\n').encode()
            + b'data: [DONE]\n\n')
    return httpx.Response(200, headers={'content-type': 'text/event-stream'}, content=body)


def factory(scope):
    return ChatHTTPModelAdapter(
        ChatHTTPConfig('https://fixture.invalid/chat/completions', 'synthetic-usage-fixture',
                       credential_env='PER_PULSE_USAGE_QA_KEY'),
        transport=httpx.MockTransport(handler),
    )


app = create_app(state, model_factory=factory)


if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('PER_PULSE_QA_PORT', '8152')), log_level='warning')
