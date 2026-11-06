"""Connect one World's weekly allowance to the existing Kernel model factory.

The gate is installed before the PageService starts. It does not create a
second Kernel, provider client, or runtime, and cannot be supplied by model
content. Every provider adapter created by the original factory inherits
the same World-owned source resolver and persistent request ledger.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

from pulse_system.service.weekly_requests import (
    WeeklyRequestBudget,
    WeeklyRequestGate,
    world_source_for_scope,
)


def install_world_request_gate(pages) -> WeeklyRequestBudget:
    """Bind physical HTTP attempt accounting before any runtime turn starts.

    A crash may leave a dispatched send whose result is unknown. Reconcile
    that receipt before the next physical send rather than silently replaying
    it for free. Unsent reservations may be abandoned on recovery.
    """
    runtime = pages.runtime
    storage = runtime.storage
    ledger = runtime.causal_ledger
    world_id = runtime.world_id
    budget = WeeklyRequestBudget(storage, world_id)
    runtime._require_runtime_owner()
    budget.reconcile()

    # kernel_service is the public _WorldKernelService facade (messages,
    # approvals, provenance). It deliberately does not own model adapters.
    # Bind the actual existing KernelHarnessRuntime created by the one host.
    from pulse_system.service.kernel_runtime import KernelHarnessRuntime
    kernel = runtime._harness
    if type(kernel) is not KernelHarnessRuntime:
        raise RuntimeError("Per Pulse weekly admission requires the native Kernel")
    if getattr(kernel, "_per_pulse_weekly_gate", None) is not None:
        raise RuntimeError("native request admission is already installed")

    original_factory = kernel._model_factory
    gate = WeeklyRequestGate(
        budget, lambda scope: world_source_for_scope(ledger, world_id, scope),
    )
    if original_factory is not None:
        def metered_factory(scope):
            adapter = original_factory(scope)
            if adapter is None:
                return None
            try:
                adapter.bind_attempt_accounting(gate)
            except BaseException:
                adapter.close()
                raise
            return adapter

        kernel._model_factory = metered_factory

    kernel._per_pulse_weekly_gate = gate
    return budget
