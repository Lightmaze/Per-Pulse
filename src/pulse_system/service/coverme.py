"""Optional CoverMe binding: one durable, tool-less fork before publication.

The operator selects a Wiki snapshot outside the mutable workspace. The fork
borrows the main context's guarded provider; it is neither a new Pulse subject
nor a second provider configuration. Its separate trace keeps review prompts
and provider usage out of the subject's ordinary conversation history.
"""
from __future__ import annotations

from dataclasses import replace
import hashlib
import os
from pathlib import Path
from threading import Event

from pulse_harness.core import HarnessScope, HarnessStore, ModelRoundStatus, RecoveryManager, TranscriptStore, TurnRunner, TurnStatus
from pulse_harness.core.errors import CapabilityDenied, ProviderProtocolError, ProviderUnavailable, StoreUnavailable
from pulse_harness.core.replay import bound_text, digest_json
from pulse_harness.core.response_review import RESPONSE_REVIEW_PREFIX, ResponseReview, decode_review_envelope, review_receipts
from pulse_harness.plugins.coverme import CoverMeLimits, CoverMeReviewer, WikiIndex


def load_selected_wiki(workspace: Path) -> WikiIndex | None:
    """No reads or model calls while disabled; invalid explicit setup is loud."""
    enabled = os.environ.get("P_PULSE_COVERME", "0")
    if enabled not in {"0", "1"}:
        raise ValueError("P_PULSE_COVERME must be 0 or 1")
    if enabled == "0":
        return None
    selected = os.environ.get("P_PULSE_COVERME_WIKI", "")
    if not selected.strip():
        raise ValueError("CoverMe requires P_PULSE_COVERME_WIKI pointing to a Wiki JSON snapshot")
    try:
        path = Path(selected).expanduser().resolve(strict=True)
        if path.is_relative_to(workspace.resolve()):
            raise ValueError("CoverMe Wiki must be outside the Agent's writable workspace")
        return WikiIndex.load(path)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError("CoverMe Wiki is invalid, unreadable, or inside the writable workspace") from exc


class _NoTools:
    def descriptors(self):
        return ()

    def execute(self, call, cancellation):
        raise CapabilityDenied("CoverMe review has no tools", code="coverme_tools_forbidden")


class _ForkStream:
    def __init__(self, stream, parent_cancel: Event, child_cancel: Event):
        self.stream = stream
        self.parent_cancel = parent_cancel
        self.child_cancel = child_cancel

    def __iter__(self):
        try:
            for delta in self.stream:
                if self.parent_cancel.is_set():
                    self.child_cancel.set()
                yield delta
        finally:
            if self.parent_cancel.is_set():
                self.child_cancel.set()

    def close(self):
        return self.stream.close()


class _BorrowedModel:
    """Share cancellation/provider fencing without taking model ownership."""
    def __init__(self, model, parent_cancel: Event):
        self.model = model
        self.parent_cancel = parent_cancel

    @property
    def capabilities(self):
        return self.model.capabilities

    def preflight_stream(self):
        if self.parent_cancel.is_set():
            raise ProviderUnavailable("CoverMe parent was cancelled", code="coverme_cancelled")
        self.model.preflight_stream()

    def stream(self, request, *, cancel_event=None):
        self.preflight_stream()
        if request.tools or request.continuation_of or request.tool_results:
            raise ProviderProtocolError("CoverMe fork must be tool-less")
        stream = self.model.stream(request, cancel_event=self.parent_cancel)
        return _ForkStream(stream, self.parent_cancel, cancel_event or Event())

    def close(self):
        # The native main runner remains the HTTP client's sole owner.
        pass


class CoverMeRuntime:
    def __init__(self, trace_path: str | Path, index: WikiIndex, *, guard):
        self.index = index
        self.guard = guard
        self.store = HarnessStore(trace_path)
        try:
            RecoveryManager(self.store).recover(reason="coverme_process_restart")
        except BaseException:
            self.store.close()
            raise

    def reviewer(self, main_store, model, scope):
        # Keep the complete review prompt and the eventual host envelope below
        # the existing durable per-message bound, without cropping any hit.
        limit = min(main_store.max_payload_bytes, self.store.max_payload_bytes)
        limits = CoverMeLimits(
            max_input_bytes=max(256, min(56 * 1024, limit - 2048)),
            max_output_bytes=max(256, min(12 * 1024, limit // 4)),
            max_injection_bytes=max(256, min(24 * 1024, limit // 3)),
        )
        return _NativeReviewer(self, model, limits)

    def close(self):
        self.store.close()


class _NativeReviewer:
    def __init__(self, runtime: CoverMeRuntime, model, limits: CoverMeLimits):
        self.runtime = runtime
        self.model = model
        self._fork_receipt = None
        self._pure = CoverMeReviewer(runtime.index, self._fork, limits=limits)

    def review(self, request, response_text, *, round_id, cancel_event):
        self._fork_receipt = None
        result = self._pure.review(
            request, response_text, round_id=round_id, cancel_event=cancel_event
        )
        if self._fork_receipt is None:
            return result
        return replace(result, receipt={**result.receipt, "fork": self._fork_receipt})

    def _fork(self, request, prompt, round_id, cancel_event):
        self.runtime.guard()
        clean, redacted, truncated = bound_text(prompt, self.runtime.store.max_payload_bytes)
        if redacted or truncated or clean != prompt:
            raise ProviderProtocolError("CoverMe complete prompt cannot be persisted")
        source = request.scope
        identity = hashlib.sha256((source.digest + ":" + round_id).encode()).hexdigest()
        scope = HarnessScope(
            world_id=source.world_id,
            engram_id=source.engram_id,
            session_id="coverme:" + identity,
            epoch=source.epoch,
            owner_id=source.owner_id,
            context_id="coverme:" + source.context_id,
            turn_id="review:" + identity,
            capability_digest=digest_json({
                "kind": "coverme-review-only.v1",
                "source_scope": source.as_dict(),
                "source_round_id": round_id,
            }),
        )
        self._fork_receipt = {
            "trace": "coverme",
            "session_id": scope.session_id,
            "turn_id": scope.turn_id,
            "source_round_id": round_id,
        }
        runner = TurnRunner(
            self.runtime.store,
            model_adapter=_BorrowedModel(self.model, cancel_event),
            tool_provider=_NoTools(),
            max_model_rounds=1,
            max_model_stream_bytes=128 * 1024,
        )
        try:
            # An identical request/scope can reuse a completed fork. Recovery
            # before the parent's atomic review commit is still uncertain;
            # this does not turn that window into automatic main continuation.
            # Failed/uncertain forks are not silently issued again.
            outcome = runner.run(scope, prompt=prompt)
            rounds = self.runtime.store.model_rounds_for_turn(scope)
            self._fork_receipt["round_ids"] = [item.round_id for item in rounds]
            self._fork_receipt["rounds"] = [
                {"round_id": item.round_id, "status": item.status.value,
                 "provider_usage": dict(item.provider_usage) if item.provider_usage is not None else None,
                 "provider_response_id": item.provider_response_id}
                for item in rounds
            ]
            self._fork_receipt["status"] = outcome.status.value
            self.runtime.guard()
            if any(item.status is ModelRoundStatus.UNCERTAIN for item in rounds):
                raise StoreUnavailable(
                    "CoverMe fork termination cannot be confirmed",
                    code="model_stream_cleanup_uncertain",
                )
            if cancel_event.is_set():
                raise ProviderUnavailable("CoverMe parent was cancelled", code="coverme_cancelled")
            if outcome.status is not TurnStatus.SUCCEEDED:
                raise ProviderUnavailable("CoverMe fork did not finish", code="coverme_fork_failed")
            return outcome.assistant_text
        finally:
            runner.close()


def recorded_fork_usage(store: HarnessStore, scope: HarnessScope) -> tuple[dict[str, int], bool]:
    """Count provider evidence copied by the native fork owner into its receipt.

    The source-linked envelope survives restart even when CoverMe is disabled.
    A missing/failed usage record remains unaccounted, not an invented zero-cost
    review. This reads durable host receipts, never model-authored report text.
    """
    usage = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
             "cache_write_tokens": 0, "provider_requests": 0}
    accounted = True
    seen = set()
    for message in TranscriptStore(store).messages(scope):
        if message.role != "system" or not message.item_id.startswith(RESPONSE_REVIEW_PREFIX):
            continue
        envelope = decode_review_envelope(message.content)
        for receipt in review_receipts(envelope["receipt"]):
            if receipt.get("accounting_incomplete"):
                accounted = False
            fork = receipt.get("fork")
            if receipt.get("plugin") != "CoverMe" or fork is None:
                continue
            if not isinstance(fork, dict) or fork.get("source_round_id") != envelope["round_id"]:
                accounted = False
                continue
            rounds = fork.get("rounds")
            if not isinstance(rounds, list) or len(rounds) > 1:
                accounted = False
                continue
            for record in rounds:
                if not isinstance(record, dict) or not isinstance(record.get("round_id"), str):
                    accounted = False
                    continue
                if record["round_id"] in seen:
                    continue
                seen.add(record["round_id"])
                usage["provider_requests"] += 1
                values = record.get("provider_usage")
                if record.get("status") != ModelRoundStatus.COMPLETED.value or not isinstance(values, dict):
                    accounted = False
                    continue
                keys = ("input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens")
                if any(type(values.get(key)) is not int or values[key] < 0 for key in keys):
                    accounted = False
                    continue
                for key in keys:
                    usage[key] += values[key]
    return usage, accounted
