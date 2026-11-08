"""Literal approval scenario on the shared synthetic browser service fixture.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import json
import os
import httpx
import uvicorn
import fixture_server as fixture

original = fixture.handler


def handler(request):
    messages = json.loads(request.content)['messages']
    start = max(i for i, row in enumerate(messages) if row['role'] == 'user')
    current = messages[start:]
    if ('WRITE_LITERAL_APPROVAL' not in str(messages[start]['content'])
            or any(row['role'] == 'tool' for row in current)):
        return original(request)
    arguments = {'path': 'literal-approval.txt', 'expected_digest': None,
                 'content': 'The literal [REDACTED] marker is part of this file.\n'}
    chunks = [
        {'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0,
            'id': 'literal-approval', 'type': 'function', 'function': {
                'name': 'mutate_file', 'arguments': json.dumps(arguments)}}]}, 'finish_reason': None}]},
        {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls'}]},
    ]
    return httpx.Response(200, headers={'content-type': 'text/event-stream'},
        content=''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n')


fixture.handler = handler
if __name__ == '__main__':
    uvicorn.run(fixture.app, host='127.0.0.1', port=int(os.environ['PER_PULSE_QA_PORT']), log_level='warning')
