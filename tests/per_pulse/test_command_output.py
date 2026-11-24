"""Command output uses actual subprocess pipes and native action receipts.

Docker control and model choices are explicit fixtures; no provider is contacted.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from contextlib import nullcontext
import json
from pathlib import Path

import pytest

from pulse_harness.core.provider import CancellationToken
from command_output_fixture import LocalPipeCommandExecutor
from command_output_fixture import STDOUT, STDERR
from material_fixture import MaterialFixture
from test_pages import client_for, new_page, send, wait_for
from pulse_harness.adapters import command_output
from pulse_harness.core.errors import ProviderProtocolError, ScopeConflict
from pulse_system.service import kernel_runtime


def pipe_executor(root, monkeypatch, script):
    """Keep the production pump/receipt; replace only the Docker control port."""
    return LocalPipeCommandExecutor(root, script=script)


def test_long_command_keeps_both_streams_after_the_preview(tmp_path, monkeypatch):
    script = "import sys;sys.stdout.write('o'*6000+'STDOUT_FINAL_DIAGNOSIS\\n');sys.stderr.write('e'*6000+'STDERR_FINAL_DIAGNOSIS\\n')"
    executor = pipe_executor(tmp_path, monkeypatch, script)
    result = executor.execute({'argv': ['explicit-fixture']}, CancellationToken(),
        guard=lambda: None, start_guard=nullcontext)
    assert result['exit_code'] == 7
    assert result['truncated'] and len(result['stdout']) == 4096
    assert result['_captured_output']['stdout'].endswith(b'STDOUT_FINAL_DIAGNOSIS\n')
    assert result['_captured_output']['stderr'].endswith(b'STDERR_FINAL_DIAGNOSIS\n')


class OutputFixture(MaterialFixture):
    def handler(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        messages = body['messages']
        current = max(i for i, item in enumerate(messages) if item['role'] == 'user')
        instruction = messages[current]['content']
        results = [json.loads(item['content']) for item in messages[current + 1:] if item['role'] == 'tool']
        if 'READ_OUTPUT:' in instruction:
            if not results:
                return self.response(tool='read_command_output', arguments=json.loads(instruction.rsplit('READ_OUTPUT:', 1)[1]))
            self.reads.append(results[-1])
            return self.response('Saved output read. [Explicit synthetic fixture]')
        if instruction == 'RUN_OUTPUT':
            if not results:
                return self.response(tool='run_command', arguments={'argv': ['explicit-pipe-fixture']})
            return self.response('Command exited; output retained. [Explicit synthetic fixture]')
        if instruction == '':
            if not results:
                prior = [json.loads(item['content']) for item in messages[:current] if item['role'] == 'tool']
                original = next(item for item in reversed(prior) if item['output'].get('output_kind') == command_output.KIND)
                return self.response(tool='read_command_output', arguments={
                    'action_id': original['output']['output_action_id'], 'stream': 'stderr', 'offset': 4000, 'limit': 8000})
            assert 'STDERR_FINAL_DIAGNOSIS' in results[-1]['output']['content']
            self.reads.append(results[-1])
            return self.response('Pulse continued from the saved final diagnosis. [Explicit synthetic fixture]')
        return self.response('Ready. [Explicit synthetic fixture]')


def run_command(client, page_id):
    send(client, page_id, 'RUN_OUTPUT')
    waiting = wait_for(client, page_id, lambda view: bool(view['execution']['pendingActions']))
    action = waiting['execution']['pendingActions'][0]['actionId']
    approved = client.post(f'/api/pages/{page_id}/execution/actions/{action}', json={'decision': 'allow_once'})
    assert approved.status_code == 200, approved.text
    done = wait_for(client, page_id, lambda view: bool(view['execution']['commandOutputs']['items']) and not view['execution']['canCancel'])
    runtime = client.app.state.pages.runtime
    turn_id = done['execution']['commandOutputs']['items'][0]['turnId']
    wait_for(client, page_id, lambda view: bool(runtime.causal_ledger.get_turn_for_world(turn_id, runtime.world_id).result_event_id))
    return action, done['execution']['commandOutputs']['items'][0]


def test_native_output_survives_restart_and_pulse_continues_the_same_work(tmp_path, monkeypatch):
    monkeypatch.setenv('P_PULSE_COMMAND_IMAGE', 'explicit-pipe-fixture')
    monkeypatch.setattr(kernel_runtime, 'DockerCommandExecutor', LocalPipeCommandExecutor)
    fixture = OutputFixture(monkeypatch.setenv)
    with client_for(tmp_path, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        page_id = new_page(client)['page']['id']
        wait_for(client, page_id, lambda view: view['messages'][-1]['role'] == 'assistant')
        action, metadata = run_command(client, page_id)
        assert metadata['exitCode'] == 7 and metadata['streams']['stdout']['complete']
        base = f'/api/pages/{page_id}/execution/commands/{action}'
        for stream, expected in [('stdout', STDOUT), ('stderr', STDERR)]:
            chunks, offset = [], 0
            while True:
                response = client.get(f'{base}/{stream}?offset={offset}&limit=73')
                assert response.status_code == 200, response.text
                result = response.json()
                assert result['offset'] == offset and result['capture_complete']
                chunks.append(result['content'])
                offset = result['next_offset']
                if offset is None:
                    break
            assert ''.join(chunks) == expected.decode()
            downloaded = client.get(f'{base}/{stream}/download')
            assert downloaded.content == expected
            assert downloaded.headers['etag'] == '"' + metadata['streams'][stream]['sha256'] + '"'
        other = new_page(client)['page']['id']
        assert client.get(f'/api/pages/{other}/execution/commands/{action}/stdout').status_code == 404
        tick = client.post(f'/api/pages/{page_id}/tick', json={'count': 1})
        assert tick.status_code == 201, tick.text
        wait_for(client, page_id, lambda view: view['tick']['status'] == 'complete')
        assert fixture.reads[-1]['output']['action_id'] == action
        assert client.app.state.pages.runtime.harness._command_executor.calls == 1
        second_action, _ = run_command(client, page_id)
        assert second_action != action
        assert client.get(f'{base}/stdout/download').content == STDOUT
        assert client.app.state.pages.runtime.harness._command_executor.calls == 2
    monkeypatch.delenv('P_PULSE_COMMAND_IMAGE')
    # Existing output is readable with commands disabled and with no model.
    with client_for(tmp_path, model_factory=None) as client:
        result = client.get(f'{base}/stderr?offset=4000&limit=8000')
        assert result.status_code == 200 and 'STDERR_FINAL_DIAGNOSIS' in result.json()['content']
        assert client.get(f'{base}/stdout/download').content == STDOUT
        listed = client.get(f'/api/pages/{page_id}').json()['execution']['commandOutputs']['items']
        assert {item['actionId'] for item in listed} == {action, second_action}
    # A read-only model session retains this original tool and reads through
    # the normal Kernel action/continuation path, with no second approval.
    with client_for(tmp_path, model_factory=fixture.factory, allow_workspace_actions=False) as client:
        send(client, page_id, 'READ_OUTPUT:' + json.dumps({'action_id': action, 'stream': 'stdout', 'offset': 4000, 'limit': 8000}))
        wait_for(client, page_id, lambda view: view['messages'][-1]['role'] == 'assistant' and not view['execution']['canCancel'])
        assert fixture.reads[-1]['status'] == 'succeeded'
        assert 'STDOUT_FINAL_DIAGNOSIS' in fixture.reads[-1]['output']['content']


def test_capture_limit_and_raw_binary_download_keep_completeness_distinct(tmp_path, monkeypatch):
    data = b'\xff' + b'x' * (command_output.MAX_CAPTURE_BYTES + 1024)
    executor = pipe_executor(tmp_path, monkeypatch,
        f'import sys;sys.stdout.buffer.write({data[:1]!r});sys.stdout.buffer.write(b"x"*{len(data)-1})')
    result = executor.execute({'argv': ['explicit-fixture']}, CancellationToken(), guard=lambda: None, start_guard=nullcontext)
    assert len(result['_captured_output']['stdout']) == command_output.MAX_CAPTURE_BYTES
    assert result['output_bytes']['stdout'] == len(data)
    monkeypatch.setenv('P_PULSE_COMMAND_IMAGE', 'explicit-pipe-fixture')
    monkeypatch.setattr(kernel_runtime, 'DockerCommandExecutor', lambda workspace, image:
        LocalPipeCommandExecutor(workspace, script=f'import sys;sys.stdout.buffer.write({data[:1]!r});sys.stdout.buffer.write(b"x"*{len(data)-1})'))
    fixture = OutputFixture(monkeypatch.setenv)
    with client_for(tmp_path, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        page_id = new_page(client)['page']['id']
        wait_for(client, page_id, lambda view: view['messages'][-1]['role'] == 'assistant')
        action, item = run_command(client, page_id)
        assert not item['streams']['stdout']['complete']
        assert item['streams']['stdout']['savedBytes'] == command_output.MAX_CAPTURE_BYTES
        assert item['streams']['stdout']['observedBytes'] == len(data)
        base = f'/api/pages/{page_id}/execution/commands/{action}/stdout'
        assert client.get(base + '/download').content == data[:command_output.MAX_CAPTURE_BYTES]
        passage = client.get(base).json()
        assert not passage['capture_complete'] and passage['content'].startswith('\ufffd')


def test_saved_output_checks_digest_and_fits_small_payload(tmp_path, monkeypatch):
    monkeypatch.setenv('P_PULSE_COMMAND_IMAGE', 'explicit-pipe-fixture')
    monkeypatch.setattr(kernel_runtime, 'DockerCommandExecutor', LocalPipeCommandExecutor)
    fixture = OutputFixture(monkeypatch.setenv)
    with client_for(tmp_path, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        page_id = new_page(client)['page']['id']
        wait_for(client, page_id, lambda view: view['messages'][-1]['role'] == 'assistant')
        action_id, _ = run_command(client, page_id)
        runtime = client.app.state.pages.runtime
        execution = client.app.state.pages.execution
        action = execution._command_action(page_id, action_id)
        metadata = command_output.capture(action)
        root = runtime.kernel_service.checkpoint_root
        for size in (2048, 4096):
            passage = command_output.passage(root, action, 'stdout', 0, 8000, size)
            assert len(json.dumps(passage, ensure_ascii=True).encode()) <= size // 2
            assert passage['content'] and passage['next_offset'] == len(passage['content'])
        with pytest.raises(ProviderProtocolError, match='allowance'):
            command_output.passage(root, action, 'stdout', 0, 8000, 256)
        other = new_page(client)['page']['id']
        with pytest.raises(ScopeConflict):
            command_output.read(root, runtime.kernel_service.store, execution._scope(other),
                                {'action_id': action_id, 'stream': 'stdout'})
        path = root / 'command-output' / metadata['stdout']['file']
        path.write_bytes(b'changed output')
        response = client.get(f'/api/pages/{page_id}/execution/commands/{action_id}/stdout')
        assert response.status_code == 409
        assert response.json()['error']['code'] == 'command_output_unavailable'
        path.unlink()
        assert client.get(f'/api/pages/{page_id}/execution/commands/{action_id}/stdout').status_code == 409


def test_original_command_output_is_readable_after_committed_same_work_succession(tmp_path, monkeypatch):
    monkeypatch.setenv('P_PULSE_COMMAND_IMAGE', 'explicit-pipe-fixture')
    monkeypatch.setattr(kernel_runtime, 'DockerCommandExecutor', LocalPipeCommandExecutor)
    fixture = OutputFixture(monkeypatch.setenv)
    with client_for(tmp_path, model_factory=fixture.factory, allow_workspace_actions=True) as client:
        page_id = new_page(client)['page']['id']
        wait_for(client, page_id, lambda view: view['messages'][-1]['role'] == 'assistant')
        action, item = run_command(client, page_id)
        runtime = client.app.state.pages.runtime
        source_result = runtime.causal_ledger.get_turn_for_world(item['turnId'], runtime.world_id).result_event_id
        original = runtime.continuity_engram_id
        prepared = runtime._mgr.prepare_succession(original, parent_event_id=source_result,
                                                  runtime_fence=runtime._current_runtime_fence())
        rotated = runtime._mgr.commit_succession(prepared, runtime_fence=runtime._current_runtime_fence())
        assert runtime.causal_ledger.committed_history_lineage(runtime.world_id, rotated.new_id) == (rotated.new_id, original)
        assert client.get(f'/api/pages/{page_id}/execution/commands/{action}/stdout/download').content == STDOUT
        send(client, page_id, 'READ_OUTPUT:' + json.dumps({'action_id': action, 'stream': 'stdout', 'offset': 4000, 'limit': 8000}))
        wait_for(client, page_id, lambda view: bool(fixture.reads) and not view['execution']['canCancel'])
        assert fixture.reads[-1]['status'] == 'succeeded'
        assert fixture.reads[-1]['output']['action_id'] == action
        assert 'STDOUT_FINAL_DIAGNOSIS' in fixture.reads[-1]['output']['content']
        assert runtime.harness._command_executor.calls == 1


def test_command_receipt_and_followup_fit_the_configured_payload(tmp_path, monkeypatch):
    from pulse_harness.adapters.command import CommandWorkspaceToolProvider
    from pulse_harness.core import ApprovalDecision, HarnessScope
    from pulse_system.interaction.api.harness_kernel import HarnessKernelService

    script = "import sys;sys.stdout.write('\\x01'*20000);sys.stderr.write('password=synthetic-value\\n')"
    fixture = OutputFixture(monkeypatch.setenv)

    class OutputProvider(CommandWorkspaceToolProvider):
        def __init__(self, store, root, checkpoints):
            super().__init__(store, root, checkpoints,
                command_executor=LocalPipeCommandExecutor(root, script=script))

    service = HarnessKernelService(tmp_path / 'kernel.db', tmp_path / 'workspace',
        tmp_path / 'checkpoints', model_adapter_factory=fixture.factory,
        tool_provider_factory=OutputProvider, max_payload_bytes=2048)
    try:
        scope = HarnessScope('world', 'subject', 'session', 0, 'owner', context_id='work')
        service.create(scope)
        waiting = service.run_turn(scope.for_turn('command-turn'), prompt='RUN_OUTPUT')
        assert waiting['outcome']['status'] == 'waiting_approval'
        action_id = waiting['session']['turns'][-1]['actions'][0]['action_request_id']
        completed = service.resolve_approval(scope, action_id, ApprovalDecision.ALLOW_ONCE)
        assert completed['action']['status'] == 'succeeded', completed
        action = service.store.get_action(scope.for_turn('command-turn'), action_id)
        assert command_output.capture(action) is not None
        assert len(json.dumps(action.result, ensure_ascii=True).encode()) <= 2048
        assert 'synthetic-value' not in action.result['output']['stderr']
        passage = command_output.read(service.checkpoint_root, service.store, scope,
                                     {'action_id': action_id, 'stream': 'stdout', 'limit': 8000})
        assert passage['content'] and passage['next_offset'] > 0
        assert len(json.dumps(passage, ensure_ascii=True).encode()) <= 1024
    finally:
        service.close()
