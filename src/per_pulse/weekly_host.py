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
    world_source_for_event,
    world_source_for_scope,
)


def _source_for_model_request(ledger, world_id, origin_scope, request_scope, *, explicit_foreground=None):
    """Keep borrowed reviewer sends on their original trusted World turn.

    The Kernel alone creates the model adapter for origin_scope. CoverMe and
    expert posture computations borrow that adapter for tool-less sidecar
    requests with distinct trace-only scopes. Their trace turns do not exist
    in the World causal ledger, but their provider sends still belong to the
    native originating Work/SELF turn and must use its allowance. Reject
    unrelated scopes rather than interpreting a fork as free user work.
    """
    if request_scope != origin_scope:
        from pulse_harness.core.errors import ProviderUnavailable
        from pulse_harness.core.scope import HarnessScope

        def unverified():
            return ProviderUnavailable(
                "Provider request is not owned by the originating World turn.",
                code="autonomous_request_source_unverified",
            )

        if not isinstance(request_scope, HarnessScope):
            raise unverified()
        if any(getattr(request_scope, name) != getattr(origin_scope, name)
               for name in ("world_id", "engram_id", "owner_id", "epoch")):
            raise unverified()
        if request_scope.context_id not in {
            "coverme:" + origin_scope.context_id,
            "expert:" + origin_scope.context_id,
        }:
            raise unverified()
        if request_scope.context_id.startswith("coverme:"):
            prefix, turn_prefix = "coverme:", "review:"
        else:
            prefix, turn_prefix = "expert:", "compute:"
        fork_id = request_scope.session_id.removeprefix(prefix)
        if (not request_scope.session_id.startswith(prefix)
                or len(fork_id) != 64
                or any(char not in "0123456789abcdef" for char in fork_id)
                or request_scope.turn_id != turn_prefix + fork_id
                or len(request_scope.capability_digest) != 64
                or any(char not in "0123456789abcdef"
                       for char in request_scope.capability_digest)):
            raise unverified()
        # These source-bound, tool-less forks are only entered via the
        # adapter loaned by the native Kernel. The requester cannot select
        # the adapter's immutable originating scope or its request pool.
    return world_source_for_scope(ledger, world_id, origin_scope, explicit_foreground=explicit_foreground)


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

    def still_admitted() -> None:
        from pulse_system.service.weekly_requests import WeeklyRequestUnavailable
        if runtime.context_pulse.background_paused():
            raise WeeklyRequestUnavailable("background activity is paused")

    def accounting_factory(parent_scope):
        # Resolve on every physical attempt: an adapter survives multiple
        # turns, while a tool-less expert/review fork borrows its active turn.
        return WeeklyRequestGate(
            budget, lambda scope: _source_for_model_request(
                ledger, world_id, parent_scope(), scope,
                explicit_foreground=runtime.context_pulse.is_person_requested_tick),
            admission_guard=still_admitted,
        )

    def event_admitted(event, observed_at):
        from pulse_harness.core.errors import ProviderUnavailable
        try:
            source = world_source_for_event(
                ledger, world_id, event.event_id,
                explicit_foreground=runtime.context_pulse.is_person_requested_tick)
        except ProviderUnavailable:
            return False
        if source is None:
            return True
        if not pages.configured or pages.example:
            return False
        usage = budget.status(now=observed_at)
        return usage["enabled"] and usage["remaining"] > 0 and not runtime.context_pulse.background_paused()

    runtime.bind_native_provider_attempt_accounting(accounting_factory, event_admission=event_admitted)
    budget.reconcile()
    return budget
