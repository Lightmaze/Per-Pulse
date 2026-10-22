"""Pi RPC/JSONL compatibility boundary for the Pulse Harness lifecycle.

This module is intentionally the only new Harness package code that imports
the legacy Pulse Pi implementation. It adapts the old world-level runtime
without copying its session file or exposing its wire messages as core
objects. The adapter never creates a PulseWorld and never owns Engram or
succession state.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
from typing import Any
from uuid import uuid4

from pulse_harness.core.lifecycle import (
    HarnessHandle,
    HarnessRuntimePort,
    HarnessSessionRef,
)
from pulse_harness.core.scope import HarnessScope

from pulse_system.agent.harness.pi import PiHarnessRuntime


PI_ADAPTER_KIND = "pi"
_PI_ADAPTER_MARKER = "_pulse_harness_adapter_kind"
_PI_AUTHORITY_VERSION = 1
_PI_CURSOR_SOURCES = frozenset(
    {"pi_committed_turn_cursor", "adapter_durable_turn_cursor"}
)
_PI_AUTHORITY_STATES = frozenset({"READY", "RUNNING", "UNCERTAIN"})


class _PiAuthorityCapability:
    """Process-local writer capability owned by one compatibility adapter.

    This is deliberately not a Store-issued public token.  A runtime accepts
    authority writes only from the exact adapter object and this capability
    identity while the process-local adapter binding is active.  It is an
    in-process trust boundary, not a cryptographic claim against a local
    administrator with reflection access.
    """

    __slots__ = ("_nonce",)

    def __init__(self) -> None:
        self._nonce = object()

    def __reduce__(self) -> object:  # pragma: no cover - defensive boundary
        raise TypeError("Pi authority capabilities are process-local")


class PiCompatibilityError(RuntimeError):
    """A refused compatibility operation with a stable machine-readable code."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _require_runtime(runtime: object) -> HarnessRuntimePort:
    required = (
        "preflight",
        "run_turn",
        "snapshot",
        "abort",
        "steer",
        "succeed",
        "close_session",
        "close",
    )
    if any(not callable(getattr(runtime, name, None)) for name in required):
        raise TypeError("runtime does not implement the generic HarnessRuntime port")
    return runtime  # type: ignore[return-value]


def _mark_pi_runtime(runtime: HarnessRuntimePort) -> HarnessRuntimePort:
    """Attach observation metadata only; never use it as authority."""

    try:
        setattr(runtime, _PI_ADAPTER_MARKER, PI_ADAPTER_KIND)
    except Exception as exc:  # pragma: no cover - defensive foreign runtime
        raise PiCompatibilityError(
            "pi_adapter_metadata_unavailable",
            "the Pi runtime cannot carry adapter provenance",
        ) from exc
    return runtime


def _session_binding(
    runtime: HarnessRuntimePort,
    engram_id: str,
) -> Mapping[str, Any] | None:
    snapshotter = getattr(runtime, "binding_snapshot", None)
    if not callable(snapshotter):
        raise PiCompatibilityError(
            "pi_binding_port_missing",
            "the Pi compatibility runtime does not expose its durable binding reader",
        )
    snapshot = snapshotter()
    if not isinstance(snapshot, Mapping):
        raise PiCompatibilityError(
            "pi_binding_invalid",
            "the Pi binding projection is not a mapping",
        )
    sessions = snapshot.get("sessions")
    if not isinstance(sessions, Mapping):
        raise PiCompatibilityError(
            "pi_binding_invalid",
            "the Pi binding projection has no sessions mapping",
        )
    binding = sessions.get(engram_id)
    return binding if isinstance(binding, Mapping) else None


def _authority_snapshot(runtime: HarnessRuntimePort) -> Mapping[str, Any]:
    getter = getattr(runtime, "pulse_harness_authority_snapshot", None)
    if callable(getter):
        value = getter()
        if not isinstance(value, Mapping):
            raise PiCompatibilityError(
                "pi_authority_invalid",
                "the durable Pulse authority projection is not a mapping",
            )
        return value
    raise PiCompatibilityError(
        "pi_authority_port_missing",
        "the Pi runtime has no durable Pulse scope/cursor authority reader",
    )


def _authority_for(
    runtime: HarnessRuntimePort,
    engram_id: str,
) -> Mapping[str, Any] | None:
    snapshot = _authority_snapshot(runtime)
    record = snapshot.get(engram_id)
    return record if isinstance(record, Mapping) else None


def _physical_session_ref(value: Mapping[str, Any] | None) -> dict[str, str] | None:
    """Project the Pi binding identity without treating it as logical scope."""

    if value is None:
        return None
    session_id = value.get("session_id")
    session_file = value.get("session_file")
    if not isinstance(session_id, str) or not session_id.strip():
        return None
    if not isinstance(session_file, str) or not session_file.strip():
        return None
    # Pi's binding loader already canonicalizes this path.  Repeating the
    # normalization here keeps test transports from creating two spellings of
    # the same physical owner.
    canonical_file = os.path.normcase(
        os.path.abspath(os.path.normpath(os.path.expanduser(session_file)))
    )
    return {"session_id": session_id, "session_file": canonical_file}


def _physical_ref_key(value: Mapping[str, Any] | None) -> tuple[str, str] | None:
    ref = _physical_session_ref(value)
    if ref is None:
        return None
    return (ref["session_id"], ref["session_file"])


def _session_scope_dict(scope: HarnessScope) -> dict[str, object]:
    return scope.session_scope().as_dict()


def _validate_authority(
    scope: HarnessScope,
    authority: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = dict(authority)
    if authority.get("version") != _PI_AUTHORITY_VERSION:
        raise PiCompatibilityError(
            "pi_authority_invalid",
            "the Pulse authority version is unsupported",
        )
    raw_scope = authority.get("scope")
    if not isinstance(raw_scope, Mapping) or dict(raw_scope) != _session_scope_dict(scope):
        raise PiCompatibilityError(
            "pi_scope_authority_conflict",
            "the durable Pi authority is bound to another world/context/owner/epoch",
        )
    cursor = normalized.get("cursor")
    if type(cursor) is not int or cursor < 0:
        raise PiCompatibilityError(
            "pi_cursor_unavailable",
            "the durable Pi authority has no valid committed cursor",
        )
    source = normalized.get("cursor_source")
    if source not in _PI_CURSOR_SOURCES:
        raise PiCompatibilityError(
            "pi_cursor_unavailable",
            "the durable Pi authority cursor has no trusted source",
        )
    state = normalized.setdefault("authority_state", "READY")
    if state not in _PI_AUTHORITY_STATES:
        raise PiCompatibilityError(
            "pi_authority_invalid",
            "the durable Pi authority has an unsupported lifecycle state",
        )
    attempt_id = normalized.setdefault("attempt_id", "")
    if not isinstance(attempt_id, str):
        raise PiCompatibilityError(
            "pi_authority_invalid",
            "the durable Pi authority attempt identity is invalid",
        )
    if state in {"RUNNING", "UNCERTAIN"} and not attempt_id.strip():
        raise PiCompatibilityError(
            "pi_authority_invalid",
            "an active or uncertain Pi authority must have an attempt identity",
        )
    if state == "READY" and attempt_id.strip():
        raise PiCompatibilityError(
            "pi_authority_invalid",
            "a ready Pi authority cannot retain an attempt identity",
        )
    reason = normalized.setdefault("uncertainty_reason", None)
    if state == "UNCERTAIN" and (
        not isinstance(reason, str) or not reason.strip()
    ):
        raise PiCompatibilityError(
            "pi_authority_invalid",
            "an uncertain Pi authority must have a stable recovery reason",
        )
    if state != "UNCERTAIN" and reason is not None:
        raise PiCompatibilityError(
            "pi_authority_invalid",
            "the Pi authority uncertainty reason is invalid",
        )
    raw_physical = normalized.setdefault("physical_session_ref", None)
    if raw_physical is not None:
        if (
            not isinstance(raw_physical, Mapping)
            or set(raw_physical) != {"session_id", "session_file"}
            or _physical_session_ref(raw_physical) is None
        ):
            raise PiCompatibilityError(
                "pi_physical_identity_invalid",
                "the durable Pi authority physical session reference is invalid",
            )
        normalized["physical_session_ref"] = _physical_session_ref(raw_physical)
    return normalized


def _commit_authority(
    runtime: HarnessRuntimePort,
    engram_id: str,
    authority: Mapping[str, Any],
    *,
    writer: object,
    capability: object,
) -> None:
    commit = getattr(runtime, "commit_pulse_harness_authority", None)
    if not callable(commit):
        raise PiCompatibilityError(
            "pi_authority_port_missing",
            "the Pi runtime cannot durably commit Pulse scope/cursor authority",
        )
    candidate = dict(authority)
    current = _authority_for(runtime, engram_id)
    if current is not None:
        current_normalized = _validate_authority(
            HarnessScope.from_dict(dict(current["scope"])),
            current,
        )
        if candidate.get("scope") != current_normalized.get("scope"):
            raise PiCompatibilityError(
                "pi_scope_authority_conflict",
                "the Pulse authority scope is immutable after creation",
            )
        if candidate.get("cursor_source") != current_normalized.get(
            "cursor_source"
        ):
            raise PiCompatibilityError(
                "pi_authority_conflict",
                "the Pulse authority cursor source is immutable",
            )
    candidate_scope = HarnessScope.from_dict(dict(candidate["scope"]))
    candidate = _validate_authority(candidate_scope, candidate)
    if current is not None:
        old_ref = _physical_ref_key(current_normalized.get("physical_session_ref"))
        new_ref = _physical_ref_key(candidate.get("physical_session_ref"))
        if old_ref is not None and old_ref != new_ref:
            raise PiCompatibilityError(
                "pi_physical_identity_conflict",
                "the logical Pulse session is already bound to another Pi session",
            )
        current_state = current_normalized["authority_state"]
        candidate_state = candidate["authority_state"]
        current_attempt = current_normalized["attempt_id"]
        candidate_attempt = candidate["attempt_id"]
        if current_state == "READY":
            if candidate_state == "READY":
                valid = candidate == current_normalized
            elif candidate_state == "RUNNING":
                valid = (
                    candidate["cursor"] == current_normalized["cursor"]
                    and bool(candidate_attempt.strip())
                    and old_ref == new_ref
                )
            else:
                valid = False
            if not valid:
                raise PiCompatibilityError(
                    "pi_authority_transition_conflict",
                    "READY authority is idempotent or may start only one same-cursor attempt",
                )
        elif current_state == "RUNNING":
            if candidate_state == "READY":
                valid = (
                    candidate["cursor"] == current_normalized["cursor"] + 1
                    and candidate_attempt == ""
                    and candidate["uncertainty_reason"] is None
                    and (old_ref is None or old_ref == new_ref)
                )
            elif candidate_state == "UNCERTAIN":
                valid = (
                    candidate["cursor"] == current_normalized["cursor"]
                    and candidate_attempt == current_attempt
                    and old_ref == new_ref
                    and bool(candidate["uncertainty_reason"].strip())
                )
            elif candidate_state == "RUNNING":
                valid = candidate == current_normalized
            else:
                valid = False
            if not valid:
                raise PiCompatibilityError(
                    "pi_authority_transition_conflict",
                    "RUNNING authority may settle exactly once or repeat byte-identically",
                )
        elif current_state == "UNCERTAIN":
            if candidate != current_normalized:
                raise PiCompatibilityError(
                    "pi_authority_transition_conflict",
                    "UNCERTAIN authority is terminal until explicit recovery",
                )
        else:  # pragma: no cover - _validate_authority closes this first
            raise PiCompatibilityError(
                "pi_authority_transition_conflict",
                "the durable Pulse authority has an unsupported lifecycle state",
            )
    else:
        if (
            candidate["authority_state"] != "READY"
            or candidate["cursor"] != 0
            or candidate.get("attempt_id") != ""
            or candidate.get("uncertainty_reason") is not None
            or candidate.get("physical_session_ref") is not None
        ):
            raise PiCompatibilityError(
                "pi_authority_transition_conflict",
                "a new Pulse authority must begin at READY cursor zero",
            )
    physical_key = _physical_ref_key(candidate.get("physical_session_ref"))
    if physical_key is not None:
        for other_id, other in _authority_snapshot(runtime).items():
            if other_id == engram_id or not isinstance(other, Mapping):
                continue
            other_ref = _physical_ref_key(other.get("physical_session_ref"))
            if other_ref == physical_key:
                raise PiCompatibilityError(
                    "pi_physical_identity_conflict",
                    "one physical Pi session cannot serve two logical Pulse sessions",
                )
    try:
        commit(
            engram_id,
            candidate,
            _writer=writer,
            _capability=capability,
        )
    except PiCompatibilityError:
        raise
    except Exception as exc:
        raise PiCompatibilityError(
            "pi_authority_persist_failed",
            "the Pulse scope/cursor authority could not be durably committed",
        ) from exc


def _new_authority(scope: HarnessScope) -> dict[str, Any]:
    return {
        "version": _PI_AUTHORITY_VERSION,
        "scope": _session_scope_dict(scope),
        "cursor": 0,
        "cursor_source": "adapter_durable_turn_cursor",
        "authority_state": "READY",
        "attempt_id": "",
        "uncertainty_reason": None,
        "physical_session_ref": None,
    }


@dataclass(slots=True)
class PiHarnessHandle:
    """Canonical lifecycle handle over one legacy Pi session binding."""

    _runtime: HarnessRuntimePort
    _scope: HarnessScope
    _authority: dict[str, Any]
    _writer: object
    _authority_capability: object
    _recovery_evidence: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        self._runtime = _require_runtime(self._runtime)
        if not isinstance(self._scope, HarnessScope):
            raise TypeError("scope must be a HarnessScope")
        if self._writer is None or self._authority_capability is None:
            raise TypeError("Pi Harness handles require the adapter authority")
        self._authority = _validate_authority(self._scope, self._authority)
        if self._recovery_evidence is not None and not isinstance(
            self._recovery_evidence, Mapping
        ):
            raise TypeError("recovery evidence must be a mapping")

    @property
    def adapter_kind(self) -> str:
        return PI_ADAPTER_KIND

    @property
    def session_ref(self) -> HarnessSessionRef:
        snapshot = self._safe_snapshot()
        physical = _physical_session_ref(snapshot)
        expected = _physical_ref_key(self._authority.get("physical_session_ref"))
        if expected is not None and physical is not None and _physical_ref_key(physical) != expected:
            raise PiCompatibilityError(
                "pi_physical_identity_conflict",
                "the Pi snapshot belongs to a different durable physical session",
            )
        recovery = {
            "adapter_kind": PI_ADAPTER_KIND,
            "state": str(snapshot.get("state", "UNKNOWN")),
            "binding_state": snapshot.get("binding_state"),
            "cursor_source": self._authority["cursor_source"],
            "logical_session_id": self._scope.session_id,
            "physical_session_bound": expected is not None,
        }
        if self._recovery_evidence:
            recovery.update(dict(self._recovery_evidence))
        return HarnessSessionRef(
            scope=self._scope,
            # The Pulse session is the logical identity.  Pi's physical
            # session id is opaque evidence and must never be substituted for
            # it when Pi creates a fresh session.
            session_id=self._scope.session_id,
            cursor=self._authority["cursor"],
            recovery_evidence=recovery,
        )

    def run_turn(
        self,
        prompt: str,
        *,
        timeout_sec: float | None = None,
        turn_id: str | None = None,
        bootstrap_text: str | None = None,
    ) -> Any:
        if not isinstance(prompt, str):
            raise ValueError("prompt must be text")
        effective_turn_id = turn_id if turn_id is not None else self._scope.turn_id
        durable = _authority_for(self._runtime, self._scope.engram_id)
        if durable is None:
            raise PiCompatibilityError(
                "pi_authority_missing",
                "the Pi turn has no durable Pulse authority",
            )
        current = _validate_authority(self._scope, durable)
        if current["authority_state"] == "UNCERTAIN":
            raise PiCompatibilityError(
                "pi_turn_recovery_required",
                "the previous Pi turn has an unresolved physical outcome",
            )
        if current["authority_state"] != "READY":
            raise PiCompatibilityError(
                "pi_turn_in_progress",
                "the Pi authority already has a running turn",
            )
        attempt_id = uuid4().hex
        starting = dict(current)
        starting["authority_state"] = "RUNNING"
        starting["attempt_id"] = attempt_id
        starting["uncertainty_reason"] = None
        _commit_authority(
            self._runtime,
            self._scope.engram_id,
            starting,
            writer=self._writer,
            capability=self._authority_capability,
        )
        self._authority = _validate_authority(self._scope, starting)
        try:
            # The hidden keyword is an internal handoff, not a second loop.
            # PiHarnessRuntime accepts it only after the adapter's durable
            # RUNNING admission; an implementation that cannot honor the
            # handoff is refused without retrying an un-managed turn.
            result = self._runtime.run_turn(
                self._scope.engram_id,
                prompt,
                timeout_sec=timeout_sec,
                bootstrap_text=bootstrap_text,
                turn_id=effective_turn_id or None,
                _pulse_harness_authority_managed=True,
                _pulse_harness_authority_capability=self._authority_capability,
            )
        except Exception as exc:
            # Pi's prompt acknowledgement is a pre-effect admission point.
            # A structured `pi_prompt_refused` response proves that the
            # physical turn was not accepted, so release only this exact
            # attempt and keep the cursor reusable.  Timeouts, connection
            # loss, protocol errors, and every accepted/unknown failure stay
            # on the normal UNCERTAIN path.
            release = getattr(
                self._runtime,
                "release_unaccepted_pulse_harness_attempt",
                None,
            )
            if (
                getattr(exc, "code", None) == "pi_prompt_refused"
                and getattr(exc, "prompt_accepted", None) is False
                and callable(release)
            ):
                try:
                    release(
                        self._scope.engram_id,
                        starting,
                        _writer=self._writer,
                        _capability=self._authority_capability,
                    )
                except Exception:
                    self._settle_uncertain(
                        starting,
                        "pi_prompt_refusal_recovery_uncertain",
                    )
                else:
                    durable = _authority_for(
                        self._runtime,
                        self._scope.engram_id,
                    )
                    if durable is not None:
                        self._authority = _validate_authority(
                            self._scope,
                            durable,
                        )
                    raise
            else:
                self._settle_uncertain(starting, "pi_turn_failed_after_admission")
            raise

        binding = _session_binding(self._runtime, self._scope.engram_id)
        physical = _physical_session_ref(binding)
        if physical is None:
            self._settle_uncertain(starting, "pi_physical_identity_unconfirmed")
            raise PiCompatibilityError(
                "pi_physical_identity_unconfirmed",
                "the Pi turn completed without a durable physical binding",
            )
        finished = dict(starting)
        finished["authority_state"] = "READY"
        finished["attempt_id"] = ""
        finished["uncertainty_reason"] = None
        finished["cursor"] = current["cursor"] + 1
        finished["physical_session_ref"] = physical
        try:
            _commit_authority(
                self._runtime,
                self._scope.engram_id,
                finished,
                writer=self._writer,
                capability=self._authority_capability,
            )
        except Exception as exc:
            self._settle_uncertain(starting, "pi_authority_persist_uncertain")
            raise PiCompatibilityError(
                "pi_authority_persist_uncertain",
                "Pi changed its physical session but the cursor authority did not settle",
            ) from exc
        self._authority = _validate_authority(self._scope, finished)
        return result

    def _settle_uncertain(
        self,
        starting: Mapping[str, Any],
        reason: str,
    ) -> None:
        uncertain = dict(starting)
        uncertain["authority_state"] = "UNCERTAIN"
        uncertain["uncertainty_reason"] = reason
        try:
            _commit_authority(
                self._runtime,
                self._scope.engram_id,
                uncertain,
                writer=self._writer,
                capability=self._authority_capability,
            )
        except Exception:
            # The durable RUNNING admission remains the recovery fact when a
            # second write is unavailable.  It is never converted to success
            # and a new process must reconcile it before wake can proceed.
            pass
        self._authority = _validate_authority(self._scope, uncertain)

    def snapshot(self) -> Mapping[str, Any]:
        snapshot = self._safe_snapshot()
        return {
            "adapter_kind": PI_ADAPTER_KIND,
            "session_id": self._scope.session_id,
            "physical_session_id": snapshot.get("session_id"),
            "state": str(snapshot.get("state", "UNKNOWN")),
            "binding_state": snapshot.get("binding_state"),
            "bootstrapped": snapshot.get("bootstrapped"),
            "cursor": self._authority["cursor"],
            "cursor_source": self._authority["cursor_source"],
            "recovery_evidence": dict(self.session_ref.recovery_evidence),
        }

    def abort(self) -> None:
        self._runtime.abort(self._scope.engram_id)

    def steer(self, content: str) -> None:
        self._runtime.steer(self._scope.engram_id, content)

    def close(self) -> None:
        self._runtime.close_session(self._scope.engram_id)

    def _safe_snapshot(self) -> Mapping[str, Any]:
        try:
            snapshot = self._runtime.snapshot(self._scope.engram_id)
        except Exception as exc:
            if getattr(exc, "code", None) != "pi_session_unknown":
                raise
            return {
                "session_id": self._scope.session_id,
                "state": "UNBOUND",
                "binding_state": None,
                "bootstrapped": False,
            }
        if not isinstance(snapshot, Mapping):
            raise PiCompatibilityError(
                "pi_snapshot_invalid",
                "the Pi runtime returned a non-mapping session snapshot",
            )
        raw_session_id = snapshot.get("session_id")
        if raw_session_id is not None and (
            not isinstance(raw_session_id, str) or not raw_session_id.strip()
        ):
            raise PiCompatibilityError(
                "pi_session_identity_invalid",
                "the Pi session identity is not a non-empty string",
            )
        expected = _physical_ref_key(self._authority.get("physical_session_ref"))
        actual = _physical_ref_key(snapshot)
        if expected is not None and actual is not None and actual != expected:
            raise PiCompatibilityError(
                "pi_physical_identity_conflict",
                "the Pi binding belongs to a different durable physical session",
            )
        return snapshot


class PiCompatibilityAdapter:
    """Factory and lifecycle bridge for the existing Pi runtime."""

    kind = PI_ADAPTER_KIND

    def __init__(
        self,
        *,
        runtime_factory: Callable[..., HarnessRuntimePort] = PiHarnessRuntime,
        expected_owner_id: str | None = None,
        expected_epoch: int | None = None,
    ) -> None:
        if not callable(runtime_factory):
            raise TypeError("runtime_factory must be callable")
        if (expected_owner_id is None) != (expected_epoch is None):
            raise ValueError("expected owner and epoch must appear together")
        if expected_owner_id is not None and (
            not isinstance(expected_owner_id, str)
            or not expected_owner_id.strip()
            or type(expected_epoch) is not int
            or expected_epoch < 0
        ):
            raise ValueError("expected owner and epoch must be valid")
        self._runtime_factory = runtime_factory
        self._expected_owner_id = expected_owner_id
        self._expected_epoch = expected_epoch
        self._runtime: HarnessRuntimePort | None = None
        self._authority_capability = _PiAuthorityCapability()

    def bootstrap(
        self,
        *,
        workspace: object,
        config: Mapping[str, Any],
    ) -> HarnessRuntimePort:
        """Construct one Pi runtime from the adapter-owned config namespace."""

        if self._runtime is not None:
            raise PiCompatibilityError(
                "pi_runtime_already_bound",
                "one compatibility factory cannot replace its shared Runtime",
            )
        adapter_config: Mapping[str, Any] = config
        nested = config.get("adapter")
        if isinstance(nested, Mapping) and isinstance(nested.get("pi"), Mapping):
            adapter_config = nested["pi"]
        kwargs = dict(adapter_config)
        # The provider-neutral factory carries host services separately. Pi
        # is the only layer allowed to combine those with its constructor
        # configuration and unpack the historical fields.
        host = config.get("host")
        if isinstance(host, Mapping):
            kwargs.update(host)
        runtime = _require_runtime(self._runtime_factory(workspace, **kwargs))
        runtime = _mark_pi_runtime(runtime)
        self._ensure_authority_writer(runtime)
        self._runtime = runtime
        return runtime

    def create_runtime(self, workspace: object, **kwargs: Any) -> HarnessRuntimePort:
        """Adapter-local compatibility alias; RuntimeService does not call it."""

        return self.bootstrap(
            workspace=workspace,
            config={"adapter": {"pi": dict(kwargs)}},
        )

    def create(
        self,
        scope: HarnessScope,
        config: Mapping[str, Any] | None = None,
    ) -> HarnessHandle:
        self._check_scope_fence(scope)
        runtime = self._runtime_from_config(config)
        binding = _session_binding(runtime, scope.engram_id)
        authority = _authority_for(runtime, scope.engram_id)
        if binding is not None:
            # Succession persists a deliberately unmaterialized binding until
            # the successor's first physical turn.  It has a parent lineage
            # identity, but no Pi session identity to validate yet.  The
            # canonical create path may mint the logical Pulse authority for
            # that pending binding; it must not infer a physical session from
            # the caller or from the predecessor.
            pending_lineage = binding.get("state") == "pending_lineage"
            if not pending_lineage:
                self._validate_binding(scope, binding, authority)
            if authority is None:
                if not pending_lineage:
                    raise PiCompatibilityError(
                        "pi_authority_missing",
                        "an existing Pi binding has no durable Pulse scope/cursor authority",
                    )
            elif pending_lineage:
                if authority.get("physical_session_ref") is not None:
                    raise PiCompatibilityError(
                        "pi_physical_identity_conflict",
                        "a pending lineage binding cannot carry a materialized physical identity",
                    )
        if authority is None:
            authority = _new_authority(scope)
            _commit_authority(
                runtime,
                scope.engram_id,
                authority,
                writer=self,
                capability=self._authority_capability,
            )
        if authority is not None:
            authority = _validate_authority(scope, authority)
            if binding is not None and authority.get("physical_session_ref") is None:
                # A durable READY authority is immutable.  Do not turn an
                # inferred physical binding into a READY->READY write: a
                # missing physical reference is an incomplete/legacy record
                # and must be reconciled by an explicit migration boundary.
                if binding.get("state") != "pending_lineage":
                    raise PiCompatibilityError(
                        "pi_physical_identity_unconfirmed",
                        "the existing Pi binding has no matching physical authority",
                    )
        assert authority is not None
        if authority.get("authority_state") == "RUNNING":
            authority = self._recover_inflight_authority(runtime, scope, authority)
        if authority.get("authority_state") == "UNCERTAIN":
            raise PiCompatibilityError(
                "pi_turn_recovery_required",
                "the previous Pi turn has an unresolved physical outcome",
            )
        authority = _validate_authority(scope, authority)
        return PiHarnessHandle(
            runtime,
            scope,
            authority,
            self,
            self._authority_capability,
        )

    def wake(
        self,
        scope: HarnessScope,
        session_id: str,
        config: Mapping[str, Any] | None = None,
    ) -> HarnessHandle:
        self._check_scope_fence(scope)
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if session_id != scope.session_id:
            raise PiCompatibilityError(
                "pi_session_scope_conflict",
                "wake session_id differs from the HarnessScope session_id",
            )
        runtime = self._runtime_from_config(config)
        binding = _session_binding(runtime, scope.engram_id)
        if binding is None:
            raise PiCompatibilityError(
                "pi_session_unknown",
                "no durable Pi session binding exists for this Engram",
            )
        authority = _authority_for(runtime, scope.engram_id)
        if authority is None:
            raise PiCompatibilityError(
                "pi_authority_missing",
                "the durable Pi session has no Pulse scope/cursor authority",
            )
        raw_scope = authority.get("scope")
        if not isinstance(raw_scope, Mapping):
            raise PiCompatibilityError(
                "pi_scope_authority_conflict",
                "the durable Pi authority has no complete session scope",
            )
        if dict(raw_scope) != _session_scope_dict(scope):
            expected_scope = _session_scope_dict(scope)
            if set(raw_scope) != set(expected_scope):
                _validate_authority(scope, authority)
            stable_scope_keys = (
                "world_id",
                "engram_id",
                "context_id",
                "session_id",
                "turn_id",
                "capability_digest",
            )
            if any(
                raw_scope.get(key) != expected_scope.get(key)
                for key in stable_scope_keys
            ):
                # World/context/logical-session drift is never a recovery
                # handoff.  Let the normal authority validator report the
                # durable scope conflict rather than classifying it as a
                # stale Runtime lease.
                _validate_authority(scope, authority)
            else:
                recovery_permit = (
                    config.get("bootstrap_permit")
                    if isinstance(config, Mapping)
                    else None
                )
                recover = getattr(runtime, "recover_pulse_harness_authority", None)
                if not callable(recover) or recovery_permit is None:
                    raise PiCompatibilityError(
                        "harness_scope_fenced",
                        "the durable Pi session belongs to an older Runtime owner or epoch",
                    )
                try:
                    recover(
                        scope.engram_id,
                        _session_scope_dict(scope),
                        _writer=self,
                        _capability=self._authority_capability,
                        _bootstrap_permit=recovery_permit,
                    )
                except PiCompatibilityError:
                    raise
                except Exception as exc:
                    code = getattr(exc, "code", "pi_authority_recovery_failed")
                    detail = getattr(exc, "detail", str(exc))
                    raise PiCompatibilityError(str(code), str(detail)) from exc
                authority = _authority_for(runtime, scope.engram_id)
                if authority is None:
                    raise PiCompatibilityError(
                        "pi_authority_recovery_failed",
                        "the recovered Pi authority disappeared before wake",
                    )
        authority = _validate_authority(scope, authority)
        pending_lineage = binding.get("state") == "pending_lineage"
        if pending_lineage:
            if authority.get("physical_session_ref") is not None:
                raise PiCompatibilityError(
                    "pi_physical_identity_conflict",
                    "a pending lineage binding cannot carry a materialized physical identity",
                )
        else:
            self._validate_binding(scope, binding, authority)
        if authority.get("physical_session_ref") is None:
            # See create(): wake cannot silently enrich a durable READY row.
            if not pending_lineage:
                raise PiCompatibilityError(
                    "pi_physical_identity_unconfirmed",
                    "the durable Pi authority has no matching physical identity",
                )
        if authority.get("authority_state") == "RUNNING":
            authority = self._recover_inflight_authority(runtime, scope, authority)
        if authority.get("authority_state") == "UNCERTAIN":
            raise PiCompatibilityError(
                "pi_turn_recovery_required",
                "the previous Pi turn has an unresolved physical outcome",
            )
        return PiHarnessHandle(
            runtime,
            scope,
            authority,
            self,
            self._authority_capability,
            {"wake": "durable_binding", "recovery": "adapter_neutral"},
        )

    def _runtime_from_config(
        self,
        config: Mapping[str, Any] | None,
    ) -> HarnessRuntimePort:
        candidate = None if config is None else config.get("runtime")
        if candidate is not None and self._runtime is not None and candidate is not self._runtime:
            raise PiCompatibilityError(
                "pi_runtime_scope_conflict",
                "a handle cannot switch the factory to another Runtime instance",
            )
        runtime = candidate if candidate is not None else self._runtime
        if runtime is None:
            raise PiCompatibilityError(
                "pi_runtime_unavailable",
                "bootstrap must attach the shared Pulse Runtime first",
            )
        runtime = _require_runtime(runtime)
        self._ensure_authority_writer(runtime)
        if self._runtime is None:
            self._runtime = runtime
        return runtime

    def _ensure_authority_writer(self, runtime: HarnessRuntimePort) -> None:
        binder = getattr(runtime, "bind_pulse_harness_authority_writer", None)
        if not callable(binder):
            raise PiCompatibilityError(
                "pi_authority_port_missing",
                "the Pi runtime cannot bind a process-local Pulse authority writer",
            )
        try:
            binder(self, self._authority_capability)
        except PiCompatibilityError:
            raise
        except Exception as exc:
            raise PiCompatibilityError(
                "pi_authority_writer_conflict",
                "the Pi runtime rejected the canonical Pulse authority writer",
            ) from exc

    def _recover_inflight_authority(
        self,
        runtime: HarnessRuntimePort,
        scope: HarnessScope,
        authority: Mapping[str, Any],
    ) -> dict[str, Any]:
        recovered = dict(authority)
        recovered["authority_state"] = "UNCERTAIN"
        recovered["uncertainty_reason"] = "pi_turn_recovery_required"
        try:
            _commit_authority(
                runtime,
                scope.engram_id,
                recovered,
                writer=self,
                capability=self._authority_capability,
            )
        except Exception as exc:
            raise PiCompatibilityError(
                "pi_authority_recovery_failed",
                "the in-flight Pi turn could not be durably reconciled",
            ) from exc
        return recovered

    @staticmethod
    def _validate_binding(
        scope: HarnessScope,
        binding: Mapping[str, Any],
        authority: Mapping[str, Any] | None,
    ) -> None:
        state = binding.get("state")
        if not isinstance(state, str) or not state:
            raise PiCompatibilityError(
                "pi_binding_invalid",
                "durable Pi binding has no valid state",
            )
        physical = _physical_session_ref(binding)
        if physical is None:
            raise PiCompatibilityError(
                "pi_physical_identity_unconfirmed",
                "durable Pi binding has no complete physical identity",
            )
        if authority is not None:
            expected = _physical_ref_key(authority.get("physical_session_ref"))
            if expected is not None and _physical_ref_key(physical) != expected:
                raise PiCompatibilityError(
                    "pi_physical_identity_conflict",
                    "durable Pi binding changed its physical session identity",
                )

    def _check_scope_fence(self, scope: HarnessScope) -> None:
        if not isinstance(scope, HarnessScope):
            raise TypeError("scope must be a HarnessScope")
        if self._expected_owner_id is None:
            return
        if (
            scope.owner_id != self._expected_owner_id
            or scope.epoch != self._expected_epoch
        ):
            raise PiCompatibilityError(
                "harness_scope_fenced",
                "HarnessScope owner or epoch is stale for this factory",
            )
