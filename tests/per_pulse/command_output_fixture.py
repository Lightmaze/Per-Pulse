"""Real command pipe I/O with an explicit synthetic Docker control port.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import json
import sys
import threading
from types import SimpleNamespace
from uuid import uuid4

from pulse_harness.adapters.command import DockerCommandExecutor
from pulse_harness.adapters.resources import workspace_write_resource

STDOUT = ('ordinary progress café 🌍\n' * 300 + 'STDOUT_FINAL_DIAGNOSIS\n').encode()
STDERR = ('diagnostic progress café 🌍\n' * 300 + 'STDERR_FINAL_DIAGNOSIS\n').encode()


class LocalPipeCommandExecutor(DockerCommandExecutor):
    def __init__(self, workspace, image='explicit-pipe-fixture', *, script=None, exit_code=7):
        workspace.mkdir(parents=True, exist_ok=True)
        self.workspace = workspace
        self._write_resource = workspace_write_resource(workspace)
        self._lock = threading.Lock()
        self._closed = False
        self._active, self._unresolved = set(), {}
        self._env = {}
        self._prefix = [sys.executable, '-c', script or
            f'import sys;sys.stdout.buffer.write({STDOUT!r});sys.stderr.buffer.write({STDERR!r});sys.exit({exit_code})']
        self.image_id = 'sha256:' + '1' * 64
        self._config_dir = SimpleNamespace(cleanup=lambda: None)
        self.exit_code = exit_code
        self.calls = 0

    def invocation(self, *args):
        return [*self._prefix, 'create', 'explicit-local-process-fixture']

    def _control(self, *args):
        if args[0] == 'create':
            self.calls += 1
            return uuid4().hex + uuid4().hex
        return json.dumps({'Running': False, 'Status': 'exited', 'ExitCode': self.exit_code})

    def _remove(self, *args, **kwargs):
        pass
