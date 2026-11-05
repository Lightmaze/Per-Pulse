"""Native command observation browser fixture with explicit synthetic choices.

World/Kernel/SQLite, HTTP and command pipe I/O are real. Docker control and model
choices are fixtures; no external provider or Docker daemon is contacted.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import threading

import httpx
import uvicorn

from per_pulse.app import create_app
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.adapters import command_output
from pulse_system.service import kernel_runtime

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'per_pulse'))
from command_output_fixture import LocalPipeCommandExecutor, STDOUT, STDERR
from material_fixture import MaterialFixture


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--offline', action='store_true')
    args = parser.parse_args()
    args.data_dir.mkdir(parents=True, exist_ok=True)
    counts_path = args.data_dir / 'explicit-command-fixture-counts.json'
    counts = json.loads(counts_path.read_text()) if counts_path.exists() else {'commands': 0, 'requests': 0}
    lock = threading.Lock()

    def count(name):
        with lock:
            counts[name] += 1
            counts_path.write_text(json.dumps(counts))

    class BrowserPipeExecutor(LocalPipeCommandExecutor):
        def execute(self, arguments, cancellation, **kwargs):
            count('commands')
            capped = arguments['argv'][-1] == 'capped'
            failure = arguments['argv'][-1] == 'save-failure'
            script = ('import sys;sys.stdout.buffer.write(b"password="+b"X"*(1048576+128-9));sys.exit(7)' if capped else
                      'import sys;sys.stdout.write("EXPLICIT_CAPTURE_FAILURE\\n");sys.exit(7)' if failure else None)
            executor = LocalPipeCommandExecutor(self.workspace, script=script)
            return executor.execute(arguments, cancellation, **kwargs)

    def handler(request):
        count('requests')
        messages = json.loads(request.content)['messages']
        current = max(index for index, message in enumerate(messages) if message['role'] == 'user')
        task = messages[current]['content']
        if not any(name in task for name in ['RUN_OUTPUT_FIXTURE', 'RUN_CAPPED_FIXTURE', 'RUN_SAVE_FAILURE_FIXTURE']):
            return MaterialFixture.response('A later ordinary turn. [Explicit synthetic fixture]')
        results = [json.loads(message['content']) for message in messages[current + 1:] if message['role'] == 'tool']
        if not results:
            return MaterialFixture.response(tool='run_command', arguments={
                'argv': ['explicit-pipe-fixture', 'capped' if 'RUN_CAPPED_FIXTURE' in task else
                         'save-failure' if 'RUN_SAVE_FAILURE_FIXTURE' in task else 'long'],
            })
        assert results[0]['status'] == 'succeeded', 'The actual native command must settle before the fixture describes its result.'
        original = results[0]['output']
        if 'RUN_SAVE_FAILURE_FIXTURE' in task:
            assert original['exit_code'] == 7 and original.get('output_capture_error')
            return MaterialFixture.response('The command exited with 7, but saving its output failed. [Explicit synthetic fixture]')
        if 'RUN_CAPPED_FIXTURE' in task:
            return MaterialFixture.response('The command exited with 7 and reached the capture limit. [Explicit synthetic fixture]')
        passages = [result['output'] for result in results[1:] if result['output'].get('kind') == 'pulse.command-output.v1']
        if not passages:
            stream, offset = 'stdout', 0
        elif passages[-1]['next_offset'] is not None:
            stream, offset = passages[-1]['stream'], passages[-1]['next_offset']
        elif passages[-1]['stream'] == 'stdout':
            stream, offset = 'stderr', 0
        else:
            assert 'STDOUT_FINAL_DIAGNOSIS' in ''.join(p['content'] for p in passages if p['stream'] == 'stdout')
            assert 'STDERR_FINAL_DIAGNOSIS' in ''.join(p['content'] for p in passages if p['stream'] == 'stderr')
            return MaterialFixture.response('Both final diagnoses were read from the original command. [Explicit synthetic fixture]')
        return MaterialFixture.response(tool='read_command_output', arguments={
            'action_id': original['output_action_id'], 'stream': stream, 'offset': offset, 'limit': 4000,
        }, sequence=len(results))

    def factory(_scope):
        return ChatHTTPModelAdapter(ChatHTTPConfig(
            'https://fixture.invalid/chat/completions', 'explicit-command-output-fixture',
            credential_env='PER_PULSE_COMMAND_FIXTURE_KEY',
        ), transport=httpx.MockTransport(handler))

    os.environ['PER_PULSE_COMMAND_FIXTURE_KEY'] = 'explicit-synthetic-credential'
    save_output = command_output.save

    def explicit_storage_fault(checkpoints, scope, action_id, captured, counts):
        if captured['stdout'] == b'EXPLICIT_CAPTURE_FAILURE\n':
            raise OSError('Explicit synthetic command-output storage failure')
        return save_output(checkpoints, scope, action_id, captured, counts)

    command_output.save = explicit_storage_fault
    if args.offline:
        os.environ.pop('P_PULSE_COMMAND_IMAGE', None)
    else:
        os.environ['P_PULSE_COMMAND_IMAGE'] = 'explicit-pipe-fixture'
        kernel_runtime.DockerCommandExecutor = BrowserPipeExecutor
    app = create_app(args.data_dir, model_factory=None if args.offline else factory,
                     allow_workspace_actions=not args.offline, posture=False,
                     pulse=PulseSettings(enabled=False))

    @app.get('/__command/evidence')
    def evidence():
        return {**counts, 'offline': args.offline, 'streams': {
            stream: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
            for stream, data in [('stdout', STDOUT), ('stderr', STDERR)]}}

    app.router.routes.insert(0, app.router.routes.pop())
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')
