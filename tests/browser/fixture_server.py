"""Synthetic provider server used only for this sprint's browser checks."""
import json
import hashlib
import os
from pathlib import Path
import tempfile
import threading

import httpx
import uvicorn

from per_pulse.app import create_app
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter

os.environ['PER_PULSE_QA_KEY'] = 'synthetic-test-credential'
configured_state = os.environ.get('PER_PULSE_QA_DATA_DIR')
temporary = None if configured_state else tempfile.TemporaryDirectory(prefix='per-pulse-browser-')
state = Path(configured_state or temporary.name)
release = threading.Event()
entered = threading.Event()
requests = []
before = 'Draft heading.\npassword=abc\nOrdinary report content.\n'
large_before = 'DRAFT ' + 'An explicitly synthetic long report. ' * 1_400 + '\n'
document_name = 'field-note-\u6e90.md'
document = '''# A field note

This **confirmed file** keeps its source available for later work.

## Observe, then decide

1. Open the original material.
2. Keep the observation separate from its interpretation.

> A successful file write confirms the effect, not the quality of its argument.

| Source | Observation | Interpretation | Version | Context | Next step |
| --- | --- | --- | --- | --- | --- |
| Field notes | The page is readable | Test a second case | Original | Reading | Review |

[A source](https://example.org/source)
[An inert executable link](javascript:alert(1))
[A relative file](../private-note.md)

![A diagram described without fetching it](https://remote.invalid/unexpected-source-image.png)

<script>window.rawDocumentRuns = true</script>

```html
<strong>This code stays literal.</strong>
```
'''
document_revision = '# A later version\n\nPRIVATE_LATER_WORK_RESULT belongs to its own original receipt.\n'
(state / 'workspace').mkdir(exist_ok=True)
for filename, content in [('redacted.txt', before), ('large-report.txt', large_before)]:
    if not (state / 'workspace' / filename).exists():
        (state / 'workspace' / filename).write_text(content)

def handler(request):
    body = json.loads(request.content)
    requests.append(body)
    messages = body['messages']
    text = json.dumps(messages)
    has_tool = any(m.get('role') == 'tool' for m in messages)
    if 'SLOW_AFTER_APPROVAL' in text and has_tool:
        entered.set()
        release.wait(30)
    if not has_tool and 'WRITE_' in text:
        name = 'slow.txt' if 'SLOW_AFTER_APPROVAL' in text else 'declined.txt' if 'WRITE_DECLINE' in text else 'approved.txt'
        arguments = {'path': name, 'expected_digest': None, 'content': 'A real file from a synthetic model fixture.\n'}
        if 'WRITE_REDACTED' in text:
            arguments = {'path': 'redacted.txt', 'expected_digest': hashlib.sha256(before.encode()).hexdigest(), 'patch': {'edits': [{'old': 'Draft heading.', 'new': 'Final heading.'}]}}
        if 'WRITE_LARGE' in text:
            arguments = {'path': 'large-report.txt', 'expected_digest': hashlib.sha256(large_before.encode()).hexdigest(), 'patch': {'edits': [{'old': 'DRAFT', 'new': 'FINAL'}]}}
        if 'WRITE_DOCUMENT' in text:
            arguments = {'path': document_name, 'expected_digest': None, 'content': document}
        if 'WRITE_DOCUMENT_REVISION' in text:
            arguments = {'path': document_name, 'expected_digest': hashlib.sha256(document.encode()).hexdigest(), 'content': document_revision}
        delta = {'tool_calls': [{'index': 0, 'id': 'fixture-file', 'type': 'function', 'function': {
            'name': 'mutate_file', 'arguments': json.dumps(arguments)
        }}]}
        finish = 'tool_calls'
    else:
        delta = {'content': 'Synthetic model fixture: this page has finished its turn.'}
        finish = 'stop'
    chunks = [{'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]}, {'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]}]
    stream = ''.join('data: ' + json.dumps(c) + '\n\n' for c in chunks) + 'data: [DONE]\n\n'
    return httpx.Response(200, headers={'content-type':'text/event-stream'}, content=stream)

def factory(scope):
    return ChatHTTPModelAdapter(ChatHTTPConfig('https://fixture.invalid/chat/completions', 'synthetic-browser-fixture', credential_env='PER_PULSE_QA_KEY'), transport=httpx.MockTransport(handler))

app = create_app(state, model_factory=factory, allow_workspace_actions=True)

@app.get('/__fixture/status')
def status():
    return {'requests': len(requests), 'entered': entered.is_set(), 'files': sorted(p.name for p in (state / 'workspace').glob('*.txt')),
            'document': {'path': document_name, 'content': document, 'sha256': hashlib.sha256(document.encode()).hexdigest(),
                         'revision': document_revision}}

@app.get('/__fixture/release')
def unblock():
    release.set()
    return {'released': True}

# Put fixture routes before the frontend catch-all, only in this test server.
fixture_routes = app.router.routes[-2:]
del app.router.routes[-2:]
frontend_index = next(i for i, route in enumerate(app.router.routes) if getattr(route, 'path', None) == '/{route:path}')
app.router.routes[frontend_index:frontend_index] = fixture_routes

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('PER_PULSE_QA_PORT', '8146')), log_level='warning')
