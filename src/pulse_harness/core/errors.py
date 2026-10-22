"""Typed errors for the provider-neutral Harness ABI."""

from __future__ import annotations


class HarnessError(Exception):
    """Base error carrying a stable user-visible error code."""

    code = "harness_error"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class ScopeConflict(HarnessError):
    code = "scope_conflict"


class LeaseFenced(HarnessError):
    code = "lease_fenced"


class RevisionConflict(HarnessError):
    code = "revision_conflict"


class IdempotencyConflict(HarnessError):
    code = "idempotency_conflict"


class StoreUnavailable(HarnessError):
    code = "store_unavailable"


class InvalidReplayCursor(HarnessError):
    code = "invalid_replay_cursor"


class InvalidTransition(HarnessError):
    code = "invalid_transition"


class ApprovalConflict(HarnessError):
    code = "approval_conflict"


class PathViolation(HarnessError):
    code = "path_violation"


class CapabilityDenied(HarnessError):
    code = "capability_denied"


class RedactionRequired(HarnessError):
    """A high-confidence raw credential cannot cross the durable boundary."""

    code = "redaction_required"


class ProviderUnavailable(HarnessError):
    code = "provider_unconfigured"


class ProviderCapabilityMissing(HarnessError):
    code = "provider_capability_missing"


class ProviderProtocolError(HarnessError):
    code = "provider_protocol_error"


class EffectUnknown(HarnessError):
    """An external effect may have crossed its boundary without a durable terminal."""

    code = "effect_unknown"
