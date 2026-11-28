"""Optional constructive expert work inside the existing native execution.

The default enabled mode computes new task material with the configured LLM.
The earlier signed-feature selector remains an explicitly selected baseline.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from pulse_harness.core import HarnessScope, HarnessStore, ModelRoundStatus, RecoveryManager, TranscriptStore, TurnRunner, TurnStatus
from pulse_harness.core.errors import LeaseFenced, ProviderProtocolError, ScopeConflict, StoreUnavailable
from pulse_harness.core.provider import CancellationRequested
from pulse_harness.core.replay import bound_text, digest_json
from pulse_harness.core.turn_advice import TURN_ADVICE_PREFIX, TurnAdvice, decode_advice
from pulse_harness.core.response_review import RESPONSE_REVIEW_PREFIX, ResponseReview, decode_review_envelope, review_receipts
from pulse_harness.plugins.expert_computation import ExpertStore, canonical, fingerprint
from pulse_harness.plugins.expert_work import working_view, make_work_prompt, contribution_context, WorkMaterialBudgetExceeded
from pulse_harness.plugins.posture import pack_posture_materials, posture_contribution_context
from .coverme import _BorrowedModel, _NoTools
from . import native_replay


def expert_enabled() -> bool:
    value = os.environ.get("P_PULSE_EXPERT_COMPUTATION", "0")
    if value not in {"0", "1"}:
        raise ValueError("P_PULSE_EXPERT_COMPUTATION must be 0 or 1")
    return value == "1"


_TASK_COMMON = frozenset({
    "please", "create", "write", "produce", "generate", "prepare", "review",
    "making", "using", "should", "would", "could", "before", "after",
    "another", "current", "their", "there", "these", "those", "about",
})
_HAN_SPAN = re.compile(r"[\u3400-\u9fff]{2,}")
_HAN_FUNCTION_WORD = re.compile(r"[的了把将请给与和并从为]")


def _literal_task_terms(text: str) -> tuple[str, ...]:
    """Extract literal cues from a bounded user-authored passage.

    Chinese requests are often unspaced: a whole sentence is too specific
    to find in a saved lesson. Preserve short phrases and sample the opening,
    middle and end of longer Han spans. This offers retrieval candidates,
    not a language classifier or a judgment that any lesson applies.
    """
    text = text.casefold()
    words = re.findall(r"[A-Za-z][A-Za-z0-9]{5,31}", text)
    english = sorted({word for word in words if word not in _TASK_COMMON},
                     key=lambda word: (-len(word), word))
    han = []
    for span in _HAN_SPAN.findall(text):
        for part in _HAN_FUNCTION_WORD.split(span):
            if len(part) < 2:
                continue
            if len(part) <= 8:
                han.append(part)
            if len(part) > 4:
                han.extend((part[:4], part[-4:]))
            if len(part) > 12:
                middle = len(part) // 2
                han.append(part[middle - 2:middle + 2])
    if not han:
        return tuple(english[:6])
    # Keep late task objects visible; reserve half the packet for each script
    # in mixed-language requests. Repeated phrases only consume one slot.
    chinese = list(dict.fromkeys(reversed(han)))
    first = chinese[:3]
    latin = english[:6 - len(first)]
    return tuple((first + latin + chinese[3:])[:6])


def _task_terms(request) -> tuple[str, ...]:
    """Offer cues for the current purpose, with limited continuity for revisions.

    A long user message can contain source material followed by the actual
    request. Keep the closing passage in the existing 6,000-character budget
    and reserve cue slots for it. A terse follow-up can refer to the previous
    user task; borrow a few missing cues from that task only. These are
    retrieval candidates, not a resolution of intent or authority.
    """
    tasks = (row.get("content", "") for row in reversed(request.messages)
             if row.get("role") == "user")
    latest = next(tasks, "")
    if not isinstance(latest, str):
        return ()

    def bounded_terms(text):
        if len(text) <= 6000:
            return _literal_task_terms(text)
        head = _literal_task_terms(text[:4000])
        tail = _literal_task_terms(text[-2000:])
        # Keep passages separate; do not create words at the omitted seam.
        return tuple(dict.fromkeys(tail[:3] + head[:3] + tail[3:] + head[3:]))[:6]

    current = bounded_terms(latest)
    if len(latest) <= 120 and len(current) < 3:
        previous = next(tasks, "")
        if isinstance(previous, str):
            # The new request gets the first slots; previous task cues can
            # recover its object without making historical text a directive.
            return tuple(dict.fromkeys(current + bounded_terms(previous)[:3]))[:6]
    return current


def _fork_scope(source, identity):
    """The same complete source binding is used for execution and metering."""
    fork_id = fingerprint(identity)
    return HarnessScope(
        world_id=source.world_id, engram_id=source.engram_id,
        session_id="expert:" + fork_id, epoch=source.epoch, owner_id=source.owner_id,
        context_id="expert:" + source.context_id, turn_id="compute:" + fork_id,
        capability_digest=digest_json({"kind": "expert-work-no-tools.v1", "source": source.as_dict(),
                                      "decision_id": identity}),
    )


class ExpertRuntime:
    def __init__(self, path, *, guard, posture_memory=None, expert_active=True):
        self.mode = os.environ.get("P_PULSE_EXPERT_MODE", "semantic")
        if self.mode not in {"semantic", "retrieval_baseline"}:
            raise ValueError("P_PULSE_EXPERT_MODE must be semantic or retrieval_baseline")
        if posture_memory is not None and self.mode != "semantic":
            raise ValueError("posture experience requires P_PULSE_EXPERT_MODE=semantic")
        self.posture_memory = posture_memory
        self.expert_active = expert_active
        self.store = ExpertStore(path)
        self.guard, self.trace = guard, None
        self.selected_source_material = None
        try:
            if self.mode == "semantic":
                trace_path = ":memory:" if str(path) == ":memory:" else str(path) + ".trace"
                self.trace = HarnessStore(trace_path)
                if trace_path != ":memory:":
                    Path(trace_path).chmod(0o600)
                RecoveryManager(self.trace).recover(reason="expert_process_restart")
        except BaseException:
            if self.trace is not None:
                self.trace.close()
            self.store.close()
            raise

    def advisor(self, main_store, model, scope):
        return _Advisor(self, main_store, model)

    def posture_reviewer(self, main_store, model, scope):
        return _PostureReviewer(self, main_store, model)

    def bind_selected_source_material(self, reader):
        """Bind the product's native source projection before any Work runs."""
        if not callable(reader):
            raise TypeError("selected source reader must be callable")
        self.selected_source_material = reader

    def posture_materials(self, scope, *, task_terms=()):
        if self.posture_memory is None:
            return None
        materials = self.posture_memory.materials(
            subject_id=scope.engram_id, context_id=scope.context_id,
            prioritize_content=True, max_records=128, compact_backfill=16,
            task_terms=task_terms, summarize_omissions=True)
        return pack_posture_materials(materials)

    def _stale_posture_refs(self, scope, offered, *, cache=None):
        """Recheck the actual offered revisions, not today's bounded shortlist.

        An older lesson can enter a Work through task-cue rescue even though it
        is absent from a later cue-free 128+16 selection. Selection coverage
        cannot determine whether the original source remains authorized.
        Native World visibility and the exact current revision determine that.
        """
        if not offered:
            return []
        if self.posture_memory is None:
            return list(offered)
        cache = {} if cache is None else cache
        stale = []
        for ref in offered:
            identity = ref["id"]
            if identity not in cache:
                current = self.posture_memory.material_experience(
                    subject_id=scope.engram_id, context_id=scope.context_id,
                    experience_id=identity)
                cache[identity] = current["revision"] if current is not None else None
            if cache[identity] != ref["revision"]:
                stale.append(ref)
        return stale

    def work_materials(self, scope, posture_packet):
        """Reuse earlier work only while its original experience remains valid.

        An old computation may have used a source selected for its *then*
        task, rather than today's shortlist. Keep the contribution when that
        source is still visible at the same revision; discard it on withdrawal,
        revision, or loss of native subject/context access.
        """
        owner = scope.world_id + ":" + scope.engram_id
        materials = self.store.materials(owner, scope.context_id)
        cache = {}
        previous, omitted = [], []
        for record in materials["previous_computations"]:
            offered = record.get("computation", {}).get("offered_posture_revisions", [])
            stale = self._stale_posture_refs(scope, offered, cache=cache)
            if stale or (not self.expert_active and not offered):
                omitted.append(record["id"])
            else:
                previous.append(record)
        materials["previous_computations"] = previous
        if omitted:
            materials["omitted_stale_posture_decisions"] = omitted
        if not self.expert_active:
            materials["episodes"] = []
        return materials

    def current_advice(self, scope, advice):
        """Revalidate offered revisions before a new main-store admission.

        Sidecar persistence is not main admission. A restart, or feedback while
        a fork is running, must not revive withdrawn material. Keep its original
        usage receipt even when the contribution is suppressed. Already admitted
        main-store advice is replayed by TurnRunner without calling this method.
        """
        self.guard()
        offered = advice.receipt.get("offered_posture_revisions", [])
        if offered:
            stale = self._stale_posture_refs(scope, offered)
            if stale:
                return TurnAdvice(receipt={
                    **advice.receipt, "status": "suppressed_before_admission",
                    "reason": "posture_revision_no_longer_eligible",
                    "suppressed_posture_revisions": stale,
                })
        elif not self.expert_active:
            return TurnAdvice(receipt={
                **advice.receipt, "status": "suppressed_before_admission",
                "reason": "ordinary_expert_work_disabled",
            })
        return advice

    @staticmethod
    def material_budget_receipt(posture_packet, *, checkpoint):
        packet = posture_packet or {}
        return {"plugin": "expert_computation", "mode": "semantic",
                "status": "materials_exceed_budget", "checkpoint": checkpoint,
                "offered_posture_revisions": [],
                "omitted_posture_revisions": (packet.get("offered_posture_revisions", [])
                                               + packet.get("omitted_posture_revisions", []))}

    def record_outcome(self, main_store, scope, result):
        self.guard()
        if result.status.value not in {"succeeded", "failed", "cancelled"}:
            return
        actions = main_store.actions_for_turn(scope, limit=128)
        outcome = {
            "status": result.status.value, "error_code": result.error_code,
            "assistant_text": bound_text(result.assistant_text or "", 16000)[0],
            "actions": [{"id": action.action_request_id, "tool": action.tool_name,
                         "status": action.status.value, "arguments_digest": action.args_digest,
                         "result_digest": fingerprint(dict(action.result)) if action.result is not None else None}
                        for action in actions],
            "action_count": main_store.count_actions_for_turn(scope),
            "source": {"scope": scope.as_dict()}, "judgment": "unjudged",
        }
        for previous in self.store.turn_decisions(scope.as_dict()):
            if previous["outcome"] is None:
                self.store.record_outcome(previous["id"], outcome)

    def close(self):
        if self.trace is not None:
            self.trace.close()
        self.store.close()


class _Advisor:
    def __init__(self, runtime, main_store, model):
        self.runtime, self.main_store, self.model = runtime, main_store, model
        self.continues = runtime.mode == "semantic"

    def advise(self, request, *, cancel_event):
        return self.advise_with_source(
            request, source_material=None, cancel_event=cancel_event)

    def advise_with_source(self, request, *, source_material, cancel_event):
        """Use the current native source status before constructing this decision.

        The wrapper supplies one verified snapshot directly to this optional
        computation; the original request and saved Kernel events are untouched.
        """
        self.runtime.guard()
        if cancel_event.is_set():
            raise CancellationRequested("expert computation cancelled")
        scope = request.scope
        if native_replay.focused(self.main_store, scope):
            return None
        index = self.main_store.next_model_round_index(scope)
        identity = scope.digest + (f":round:{index}" if index else "")
        selected_source_digest = (
            fingerprint(source_material) if source_material is not None else None
        )
        # Reuse the paid calculation after restart, but main admission remains
        # a separate boundary and must respect current posture revisions.
        try:
            saved = self.runtime.store.decision(identity)
        except ValueError:
            saved = None
        if saved is not None:
            # A cached expert contribution from before a source revision must
            # not guide the next action. Do not spend again under the same
            # decision identity, or rewrite historical provider cost.
            if (saved.get("kind") == "semantic_computation"
                    and (saved.get("computation") or {}).get("selected_source_digest")
                    != selected_source_digest):
                return TurnAdvice(receipt={
                    "plugin": "expert_computation", "mode": "semantic",
                    "decision_id": identity, "status": "source_changed_before_admission",
                })
            return self.runtime.current_advice(scope, self._advice(saved))
        if self.continues:
            if index and not request.tool_results:
                return None
            # Host receipts bound attempts even if a sidecar write failed.
            admitted = [m for m in TranscriptStore(self.main_store).messages(scope)
                        if m.item_id.startswith(TURN_ADVICE_PREFIX)
                        and decode_advice(m.content)["receipt"].get("plugin") == "expert_computation"]
            if len(admitted) >= 2:
                return None
        posture_packet = self.runtime.posture_materials(
            scope, task_terms=_task_terms(request))
        has_posture = bool(posture_packet and posture_packet["episodes"])
        if not self.runtime.expert_active and not has_posture:
            if posture_packet and posture_packet["eligible_count"]:
                return TurnAdvice(receipt={
                    "plugin": "expert_computation", "mode": "semantic",
                    "status": "materials_exceed_budget", "checkpoint": "before_action",
                    "omitted_posture_revisions": posture_packet["omitted_posture_revisions"],
                })
            return None
        view, source = working_view(request, byte_budget=18000 if has_posture else 24000)
        situation = bound_text(canonical(view), 32000)[0]
        owner = scope.world_id + ":" + scope.engram_id
        if not self.continues:
            decision = self.runtime.store.propose(
                decision_id=identity, owner=owner, situation=situation, source=source)
            return self._advice(decision)
        source["native_model_round"] = index
        materials = self.runtime.work_materials(scope, posture_packet)
        try:
            prompt = make_work_prompt(view, materials, model_round=index,
                                      posture_packet=posture_packet if has_posture else None,
                                      selected_source_material=source_material,
                                      byte_budget=self.runtime.trace.max_payload_bytes if has_posture else None)
        except WorkMaterialBudgetExceeded:
            return TurnAdvice(receipt=self.runtime.material_budget_receipt(posture_packet, checkpoint="before_action"))
        outcome, receipt = self._compute(request, prompt, identity, cancel_event)
        context = None
        if outcome.status is TurnStatus.SUCCEEDED:
            try:
                context = (posture_contribution_context(outcome.assistant_text) if has_posture
                           else contribution_context(outcome.assistant_text))
            except ValueError:
                receipt["contribution_status"] = "invalid"
            else:
                receipt["contribution_status"] = "contributed" if context else "pass"
        else:
            receipt["contribution_status"] = "fork_failed"
        computation = {"prompt_digest": fingerprint(prompt), "fork": receipt,
                       "method": "constructive_work.v1",
                       "selected_source_digest": selected_source_digest}
        if has_posture:
            computation.update(checkpoint="before_action",
                               offered_posture_revisions=posture_packet["offered_posture_revisions"],
                               omitted_posture_revisions=posture_packet["omitted_posture_revisions"],
                               posture_applicability="model_interpreted_not_certified")
        try:
            decision = self.runtime.store.record_computation(
                decision_id=identity, owner=owner, situation=situation, source=source,
                context=context, computation=computation)
        except (sqlite3.Error, ValueError):
            # Keep provider cost and attempt provenance in the main store when
            # optional journal retention fails. Do not claim learned experience.
            self.runtime.guard()
            return self.runtime.current_advice(scope, TurnAdvice(context=context, receipt={
                "plugin": "expert_computation", "mode": "semantic",
                "decision_id": identity, "status": "journal_write_failed", **computation}))
        self.runtime.guard()
        return self.runtime.current_advice(scope, self._advice(decision))

    @staticmethod
    def _advice(decision):
        receipt = {"plugin": "expert_computation", "decision_id": decision["id"],
                   "selected": decision["selected"], "generation": decision["generation"],
                   "status": "contributed" if decision["context"] else "none"}
        if decision.get("kind") == "semantic_computation":
            receipt.update(mode="semantic", **decision["computation"])
        else:
            receipt.update(mode="retrieval_baseline", policy_digest=decision["policy_digest"],
                           corpus_digest=decision["corpus_digest"], explored=decision["explored"],
                           encoder=decision["encoder"])
        return TurnAdvice(context=decision["context"], receipt=receipt)

    def _compute(self, request, prompt, identity, cancel_event):
        self.runtime.guard()
        clean, redacted, truncated = bound_text(prompt, self.runtime.trace.max_payload_bytes)
        if redacted or truncated or clean != prompt:
            raise ProviderProtocolError("expert working material cannot be persisted in full")
        source = request.scope
        scope = _fork_scope(source, identity)
        runner = TurnRunner(self.runtime.trace, model_adapter=_BorrowedModel(self.model, cancel_event),
                            tool_provider=_NoTools(), max_model_rounds=1, max_model_stream_bytes=64 * 1024)
        try:
            outcome = runner.run(scope, prompt=prompt)
            rounds = self.runtime.trace.model_rounds_for_turn(scope)
            self.runtime.guard()
            if any(record.status is ModelRoundStatus.UNCERTAIN for record in rounds):
                raise StoreUnavailable("expert fork termination cannot be confirmed",
                                       code="model_stream_cleanup_uncertain")
            if cancel_event.is_set():
                raise CancellationRequested("expert computation cancelled")
            receipt = {"trace": "expert", "session_id": scope.session_id, "turn_id": scope.turn_id,
                       "source_scope_digest": source.digest, "status": outcome.status.value,
                       "rounds": [{"round_id": record.round_id, "status": record.status.value,
                                   "provider_usage": dict(record.provider_usage) if record.provider_usage is not None else None,
                                   "provider_response_id": record.provider_response_id} for record in rounds]}
            return outcome, receipt
        finally:
            runner.close()


class _PostureReviewer:
    """One semantic check of the actual candidate using the existing fork.

    A review may contribute a continuation. It never vetoes tool permissions,
    manufactures an artifact inspection, or declares the continuation correct.
    """
    def __init__(self, runtime, main_store, model):
        self.runtime, self.main_store, self.model = runtime, main_store, model

    def _source_material(self, scope):
        reader = self.runtime.selected_source_material
        return reader(scope) if reader is not None else None

    @staticmethod
    def _changed_source(advice, latest, reason):
        # Retain the paid fork receipt. A changed source requests an ordinary
        # continuation with current evidence, never a replay of an effect.
        return ResponseReview(context=latest, receipt={
            **advice.receipt, "status": "suppressed_before_admission",
            "reason": reason,
        })

    def _checked_review(self, scope, original, advice):
        latest = self._source_material(scope)
        if latest != original:
            return self._changed_source(advice, latest, "selected_source_changed_during_review")
        return ResponseReview(context=advice.context, receipt=advice.receipt)

    def review(self, request, response_text, *, round_id, cancel_event):
        self.runtime.guard()
        if cancel_event.is_set():
            raise CancellationRequested("posture review cancelled")
        identity = request.scope.digest + ":posture-review:" + fingerprint(round_id)
        helper = _Advisor(self.runtime, self.main_store, self.model)
        source_material = self._source_material(request.scope)
        source_digest = fingerprint(source_material) if source_material is not None else None
        try:
            saved = self.runtime.store.decision(identity)
        except ValueError:
            saved = None
        if saved is not None:
            advice = self.runtime.current_advice(request.scope, helper._advice(saved))
            if (saved.get("computation") or {}).get("selected_source_digest") != source_digest:
                return self._changed_source(advice, source_material, "selected_source_changed_before_admission")
            return self._checked_review(request.scope, source_material, advice)
        # The final candidate needs the same task-matched Work A lessons that
        # informed the pre-action decision. A cue-free recent shortlist can
        # evict an older, relevant lesson before the deliverable is reviewed.
        posture_packet = self.runtime.posture_materials(
            request.scope, task_terms=_task_terms(request))
        if not posture_packet or not posture_packet["episodes"]:
            return ResponseReview(receipt={"plugin": "expert_computation", "mode": "semantic",
                                           "checkpoint": "delivery",
                                           "status": ("materials_exceed_budget" if posture_packet and posture_packet["eligible_count"]
                                                      else "no_eligible_experience"),
                                           "omitted_posture_revisions": (posture_packet or {}).get("omitted_posture_revisions", [])})
        view, source = working_view(request, byte_budget=14000)
        source.update(native_model_round=self.main_store.next_model_round_index(request.scope),
                      candidate_response_digest=fingerprint(response_text), source_round_id=round_id)
        owner = request.scope.world_id + ":" + request.scope.engram_id
        materials = self.runtime.work_materials(request.scope, posture_packet)
        materials["episodes"] = []
        try:
            prompt = make_work_prompt(view, materials, model_round=source["native_model_round"],
                                      posture_packet=posture_packet, review_candidate=response_text,
                                      selected_source_material=source_material,
                                      byte_budget=self.runtime.trace.max_payload_bytes)
        except WorkMaterialBudgetExceeded:
            return ResponseReview(receipt=self.runtime.material_budget_receipt(posture_packet, checkpoint="delivery"))
        outcome, receipt = helper._compute(request, prompt, identity, cancel_event)
        receipt["source_round_id"] = round_id
        context = None
        if outcome.status is TurnStatus.SUCCEEDED:
            try:
                context = posture_contribution_context(outcome.assistant_text, review=True)
            except ValueError:
                receipt["contribution_status"] = "invalid"
            else:
                receipt["contribution_status"] = "contributed" if context else "pass"
        else:
            receipt["contribution_status"] = "fork_failed"
        computation = {
            "prompt_digest": fingerprint(prompt), "fork": receipt,
            "method": "constructive_work.v1", "checkpoint": "delivery",
            "selected_source_digest": source_digest,
            "offered_posture_revisions": posture_packet["offered_posture_revisions"],
            "omitted_posture_revisions": posture_packet["omitted_posture_revisions"],
            "posture_applicability": "model_interpreted_not_certified",
            "continuation_limit": 1,
        }
        try:
            decision = self.runtime.store.record_computation(
                decision_id=identity, owner=owner,
                situation=bound_text(canonical(view), 32000)[0], source=source,
                context=context, computation=computation)
        except (sqlite3.Error, ValueError):
            self.runtime.guard()
            advice = self.runtime.current_advice(request.scope, TurnAdvice(context=context, receipt={
                "plugin": "expert_computation", "mode": "semantic", "decision_id": identity,
                "status": "journal_write_failed", **computation}))
            return self._checked_review(request.scope, source_material, advice)
        self.runtime.guard()
        advice = self.runtime.current_advice(request.scope, helper._advice(decision))
        return self._checked_review(request.scope, source_material, advice)


def result_posture_offers(store, scope, experience_id):
    """Read pre-action offers from the exact turn, not proof of use or benefit."""
    revisions = set()
    for message in TranscriptStore(store).messages(scope):
        if message.role != "system" or not message.item_id.startswith(TURN_ADVICE_PREFIX):
            continue
        envelope = decode_advice(message.content)
        receipt = envelope["receipt"]
        fork = receipt.get("fork")
        if (envelope["scope_digest"] != scope.digest
                or receipt.get("plugin") != "expert_computation"
                or receipt.get("mode") != "semantic"
                or receipt.get("checkpoint") != "before_action"
                or not isinstance(fork, dict) or fork.get("source_scope_digest") != scope.digest
                or fork.get("status") != TurnStatus.SUCCEEDED.value):
            continue
        for ref in receipt.get("offered_posture_revisions", []):
            if (isinstance(ref, dict) and ref.get("id") == experience_id
                    and type(ref.get("revision")) is int and ref["revision"] > 0):
                revisions.add(ref["revision"])
    return sorted(revisions)


def recorded_expert_usage(store, scope, *, trace=None):
    """Meter source-bound fork attempts, without granting learning credit.

    Without ``trace`` this compatibility interface reads only host-admitted
    receipts. Production callers pass the existing expert trace so cancellation
    or a crash before advice admission cannot make a real attempt disappear.
    A durable cancelled/unfinished round counts as an attempted request, with
    unknown tokens and ``accounted=False``; it is not a zero-cost assertion.
    """
    usage = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
             "cache_write_tokens": 0, "provider_requests": 0}
    accounted, seen = True, {}

    def include_round(record):
        nonlocal accounted
        if not isinstance(record, dict) or not isinstance(record.get("round_id"), str) or not record["round_id"]:
            accounted = False
            return
        identity = record["round_id"]
        observation = {"status": record.get("status"), "provider_usage": record.get("provider_usage")}
        if identity in seen:
            if seen[identity] != observation:
                accounted = False
            return
        seen[identity] = observation
        usage["provider_requests"] += 1
        values = record.get("provider_usage")
        keys = ("input_tokens", "output_tokens", "cached_tokens", "cache_write_tokens")
        if (record.get("status") != ModelRoundStatus.COMPLETED.value or not isinstance(values, dict)
                or any(type(values.get(key)) is not int or values[key] < 0 for key in keys)):
            accounted = False
            return
        for key in keys:
            usage[key] += values[key]

    for message in TranscriptStore(store).messages(scope):
        if message.role != "system":
            continue
        if message.item_id.startswith(TURN_ADVICE_PREFIX):
            envelope = decode_advice(message.content)
            receipts = (envelope["receipt"],)
            source_valid = envelope["scope_digest"] == scope.digest
            source_round_id = None
        elif message.item_id.startswith(RESPONSE_REVIEW_PREFIX):
            envelope = decode_review_envelope(message.content)
            receipts = review_receipts(envelope["receipt"])
            source_valid = True
            source_round_id = envelope["round_id"]
        else:
            continue
        for receipt in receipts:
            if receipt.get("accounting_incomplete"):
                accounted = False
            if receipt.get("plugin") != "expert_computation" or receipt.get("mode") != "semantic":
                continue
            fork = receipt.get("fork")
            if fork is None and receipt.get("status") in {"no_eligible_experience", "materials_exceed_budget"}:
                continue
            if not source_valid or not isinstance(fork, dict) or fork.get("source_scope_digest") != scope.digest:
                accounted = False
                continue
            if source_round_id is not None and fork.get("source_round_id") != source_round_id:
                accounted = False
                continue
            rounds = fork.get("rounds")
            if not isinstance(rounds, list) or not 1 <= len(rounds) <= 1:
                accounted = False
                continue
            for record in rounds:
                include_round(record)
    if trace is not None:
        # An advisor runs before the next main model round exists; a delivery
        # reviewer runs while its source main round is still streaming. Include
        # both boundaries, and reconstruct their exact deterministic scopes.
        main_rounds = store.model_rounds_for_turn(scope)
        indices = {0, store.next_model_round_index(scope), *(item.round_index for item in main_rounds)}
        identities = [scope.digest + (f":round:{index}" if index else "") for index in sorted(indices)]
        identities += [scope.digest + ":posture-review:" + fingerprint(item.round_id) for item in main_rounds]
        for identity in identities:
            expected = _fork_scope(scope, identity)
            try:
                # Avoid catalog scans across another task's raw history. The
                # indexed existence probe is followed by the store's complete
                # scope/capability/integrity validation before reading usage.
                with trace._lock:
                    trace._require_open()
                    exists = trace._db.execute("SELECT 1 FROM sessions WHERE session_id=?", (expected.session_id,)).fetchone()
                    if exists is None:
                        continue
                    rounds = trace.model_rounds_for_turn(expected)
                for record in rounds:
                    include_round({"round_id": record.round_id, "status": record.status.value,
                                   "provider_usage": dict(record.provider_usage) if record.provider_usage is not None else None})
            except (sqlite3.Error, LeaseFenced, ScopeConflict, StoreUnavailable):
                # The caller can still show already-proven costs, but cannot
                # claim complete accounting when the source trace is unreadable.
                accounted = False
    return usage, accounted
