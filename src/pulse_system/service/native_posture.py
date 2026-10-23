"""Learn a contextual lesson from a native result and later user follow-up.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

The model supplies an interpretation. The host supplies the original encounter,
source identities, and action receipt. Remembering is staged until the native
turn succeeds; it does not award usefulness or transfer private context.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import threading
from typing import Any, Mapping

from pulse_harness.core.contracts import ActionStatus, ToolDescriptor, TurnStatus
from pulse_harness.core.errors import RedactionRequired, ScopeConflict
from pulse_harness.core.replay import bound_text
from pulse_harness.core.scope import HarnessScope
from pulse_system.core.types import (
    CausalEventDomain, CausalEventKind, CausalEventSource, CausalEventStatus,
    EngramStatus, HarnessTurnState,
)

from .posture import PostureMemory

NAME = "posture"
PREFIX = "native-posture-v1:"
MAX_PENDING = 64
_REMEMBER_FIELDS = {
    "source_message_id", "feedback_message_id", "title", "lesson",
    "conditions", "counterexamples", "shift",
}
_FEEDBACK_FIELDS = {"experience_id", "expected_revision", "verdict", "feedback_message_id"}
_NATIVE_VERDICTS = {"not_useful", "harmful", "withdrawn"}
SCHEMA = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": ["list", "remember", "feedback"]},
        "experience_id": {"type": "string", "maxLength": 160},
        "expected_revision": {"type": "integer", "minimum": 1},
        "verdict": {"type": "string", "enum": sorted(_NATIVE_VERDICTS)},
        "source_message_id": {"type": "string", "maxLength": 160},
        "feedback_message_id": {"type": "string", "maxLength": 160},
        "title": {"type": "string", "maxLength": 240},
        "lesson": {"type": "string", "maxLength": 4000},
        "conditions": {
            "description": "A condition in words, or an all/any/not tree of conditions. "
                           "These are interpreted in context, not matched as keywords.",
            "anyOf": [{"type": "string"}, {"type": "object"}],
        },
        "counterexamples": {"type": "array", "minItems": 1, "maxItems": 8,
                            "items": {"type": "string", "maxLength": 2000}},
        "shift": {"type": "object", "properties": {
            key: {"type": "string", "maxLength": 2400}
            for key in ("understanding", "candidates", "delivery")
        }, "required": ["understanding", "candidates", "delivery"], "additionalProperties": False},
    },
    "required": ["op"], "additionalProperties": False,
}


def descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        NAME,
        "Keep useful experience from this context's actual work and later user feedback. "
        "list returns applicable-scope memories and recent original message IDs. "
        "remember requires an earlier assistant result ID and a later user feedback "
        "message ID from this same context. Interpret what that feedback changes in "
        "understanding, candidate actions, and delivery, and state the conditions and "
        "a counterexample. The host retrieves the original encounter; do not invent "
        "a source or treat your self-assessment as user feedback. The host verifies "
        "message provenance and ordering, not whether the message is a correction. "
        "Do not treat an unrelated later message as feedback. Conditions need "
        "not hold everywhere, and a routine approach can be correct. Remember only "
        "a useful reusable correction, not every message. A remember call stages a "
        "context-local experience; it is saved only after this turn succeeds. "
        "If a later user correction invalidates an existing local lesson, use "
        "feedback(experience_id, expected_revision, verdict, feedback_message_id). "
        "Its evidence is the attributed later user message, with your interpretation. "
        "Use not_useful for a "
        "limited failure, harmful for a damaging lesson, or withdrawn when the "
        "lesson should stop. harmful and withdrawn disable it. This tool cannot "
        "award useful feedback from your own assessment. "
        "Finish the current work instead of polling the pending record. Saving is "
        "not evidence of improved ability. This tool cannot promote experiences "
        "to other contexts, grant tools, change permissions, or schedule new work.",
        capability="posture.internal", source="pulse.native-posture",
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def validate(arguments: Mapping[str, Any]) -> dict:
    if not isinstance(arguments, Mapping):
        raise ValueError("posture arguments must be an object")
    values = deepcopy(dict(arguments))
    if values == {"op": "list"}:
        return values
    if values.get("op") == "feedback":
        if set(values) != _FEEDBACK_FIELDS | {"op"}:
            raise ValueError("incorrect posture feedback fields")
        for name in ("experience_id", "feedback_message_id"):
            value = values[name]
            if not isinstance(value, str) or not value.strip() or len(value) > 160 or "\x00" in value:
                raise ValueError(f"invalid {name}")
        if type(values["expected_revision"]) is not int or values["expected_revision"] < 1:
            raise ValueError("expected_revision must be a positive integer")
        if values["verdict"] not in _NATIVE_VERDICTS:
            raise ValueError("native feedback cannot award positive usefulness")
        return values
    if values.get("op") != "remember" or set(values) != _REMEMBER_FIELDS | {"op"}:
        raise ValueError("unknown posture operation or fields")
    for name in ("source_message_id", "feedback_message_id"):
        value = values[name]
        if not isinstance(value, str) or not value.strip() or len(value) > 160 or "\x00" in value:
            raise ValueError(f"invalid {name}")
    # The shared validator owns all lesson, condition, and counterexample limits.
    PostureMemory.validate_experience(
        subject_id="validation", context_id="home", encounter={
            "situation": "Validation only.", "action": "Validation only.", "outcome": "Validation only.",
        }, source={"kind": "validation_only"},
        **{key: values[key] for key in _REMEMBER_FIELDS - {"source_message_id", "feedback_message_id"}},
    )
    return values


def _context_center(context_id: str) -> str | None:
    if context_id in {"", "home"}:
        return None
    if context_id.startswith("work:") and context_id[5:]:
        return context_id[5:]
    raise ScopeConflict("posture requires an owned Home or Work context")


def _source_text(value: str, limit: int):
    try:
        return bound_text(value, limit)
    except RedactionRequired as error:
        raise ScopeConflict("the selected source contains a credential and cannot enter experience memory") from error


def capture_encounter(runtime, subject_id: str, context_id: str,
                      source_message_id: str, feedback_message_id: str,
                      *, latest_input_id: str | None = None) -> dict:
    """Capture real same-context evidence for either model or operator admission.

    The caller establishes ownership of the subject/context. This helper proves
    the source is a settled native assistant result, the feedback is a later
    attributed user stimulus, and both belong to that exact World and context. A
    native caller bounds feedback by its current input; an operator may select
    any already-persisted feedback in the context.
    """
    r = runtime
    center = _context_center(context_id)
    lineage = set(r.causal_ledger.committed_history_lineage(r.world_id, subject_id))

    def message(event_id):
        event = r.causal_ledger.get_event(event_id)
        if (event is None or event.world_id != r.world_id or event.engram_id not in lineage
                or event.center_id != center or not event.content):
            raise ScopeConflict("posture source is not a message in this context")
        return event

    original, feedback = message(source_message_id), message(feedback_message_id)
    if (original.kind is not CausalEventKind.ASSISTANT_RESULT
            or original.domain is not CausalEventDomain.HARNESS
            or original.source is not CausalEventSource.SELF
            or original.status is not CausalEventStatus.SETTLED
            or feedback.kind is not CausalEventKind.STIMULUS
            or feedback.source is not CausalEventSource.USER
            or original.seq is None or feedback.seq is None
            or not original.seq < feedback.seq):
        raise ScopeConflict("remember requires an earlier assistant result and later actual user feedback")
    if latest_input_id is not None:
        latest = r.causal_ledger.get_event(latest_input_id)
        if (latest is None or latest.world_id != r.world_id or latest.engram_id != subject_id
                or latest.center_id != center or latest.seq is None or feedback.seq > latest.seq):
            raise ScopeConflict("feedback was not available to the originating turn")
    with r.storage._lock:
        previous = r.storage._conn.execute(
            "SELECT id, event_id, engram_id, state, execution_context, prompt_accepted "
            "FROM harness_turns WHERE result_event_id=?", (original.id,),
        ).fetchall()
    if (len(previous) != 1 or previous[0][1] != original.parent_event_id
            or previous[0][2] != original.engram_id or previous[0][3] != HarnessTurnState.SETTLED.value
            or previous[0][5] != 1):
        raise ScopeConflict("original result lacks its settled native turn")
    prior_turn = r.causal_ledger.get_turn_for_world(previous[0][0], r.world_id)
    if (prior_turn is None or prior_turn.result_event_id != original.id
            or r._native_kernel_context_for_turn(original.engram_id, prior_turn.id) != context_id):
        raise ScopeConflict("original result has inconsistent World provenance")
    situation = message(original.parent_event_id)
    evidence_class = str(original.metadata.get("evidence_class", "UNKNOWN"))
    synthetic = evidence_class in {"TEST_DOUBLE", "EXPLICIT_MOCK"}
    source = {
        "kind": "synthetic_runtime_observation" if synthetic else "native_user_followup",
        "provider_evidence_class": evidence_class,
        "feedback_evidence_class": "attributed_user_stimulus_not_independently_verified",
        "world_id": r.world_id,
        "subject_id": subject_id, "context_id": context_id,
        "source_subject_id": original.engram_id, "feedback_subject_id": feedback.engram_id,
        "source_message_id": original.id, "feedback_message_id": feedback.id,
        "original_turn_id": previous[0][0],
        "content_digests": {"situation": _digest(situation.content),
                            "action": _digest(original.content), "outcome": _digest(feedback.content)},
    }
    encounter, shortened = {}, []
    for key, content in (("situation", situation.content), ("action", original.content),
                         ("outcome", feedback.content)):
        clean, redacted, truncated = _source_text(content, 12000)
        encounter[key] = clean
        if redacted or truncated:
            shortened.append(key)
    source["bounded_encounter_fields"] = shortened
    return {"encounter": encounter, "source": source,
            "record_type": "constructed_fixture" if synthetic else "observed_episode"}


class NativePosture:
    """A lifecycle-owned bridge using the existing World component state."""

    def __init__(self, runtime):
        self.runtime = runtime
        self._lock = threading.RLock()

    def _memory(self) -> PostureMemory:
        memory = getattr(self.runtime.harness, "posture_memory", None)
        if memory is None:
            raise ScopeConflict("posture experience is not enabled")
        return memory

    def _requests(self, *, pending=False, subject=None, limit=64) -> list[dict]:
        storage = self.runtime.storage
        clauses, args = ["component GLOB ?"], [PREFIX + "*"]
        if pending:
            clauses.append("json_extract(state, '$.status') = 'prepared'")
        if subject is not None:
            clauses.append("json_extract(state, '$.subject') = ?")
            args.append(subject)
        args.append(limit)
        with storage._lock:
            rows = storage._conn.execute(
                "SELECT state FROM component_state WHERE " + " AND ".join(clauses) +
                " ORDER BY updated_at, component LIMIT ?", args,
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    @staticmethod
    def _center(scope) -> str | None:
        return _context_center(scope.context_id)

    def _live_turn(self, scope):
        r = self.runtime
        turn = r.causal_ledger.get_turn_for_world(scope.turn_id, r.world_id)
        if (scope.world_id != r.world_id or turn is None or turn.engram_id != scope.engram_id
                or turn.state is not HarnessTurnState.RUNNING
                or r._native_kernel_context_for_turn(scope.engram_id, turn.id) != scope.context_id):
            raise ScopeConflict("posture requires its live originating context turn")
        return turn

    def _capture(self, scope, values, turn, request_id, action_id) -> dict:
        capture = capture_encounter(
            self.runtime, scope.engram_id, scope.context_id,
            values["source_message_id"], values["feedback_message_id"],
            latest_input_id=turn.event_id,
        )
        source = {**capture["source"], "learning_turn_id": scope.turn_id,
                  "source_action_id": action_id, "native_request_id": request_id,
                  "arguments_digest": _digest(values),
                  "interpretation": "model_proposed_lesson_from_user_followup"}
        encounter = capture["encounter"]
        return {"subject_id": scope.engram_id, "context_id": scope.context_id,
                "experience_id": "posture-native-" + request_id.split(":")[-1][:40],
                "encounter": encounter, "source": source, "scope": "context",
                "record_type": capture["record_type"],
                **{key: values[key] for key in _REMEMBER_FIELDS - {"source_message_id", "feedback_message_id"}}}

    def _capture_feedback(self, scope, values, turn, request_id, action_id) -> dict:
        r, memory = self.runtime, self._memory()
        record = memory.get_experience(values["experience_id"])
        if (not memory.owns_subject(scope.engram_id, record["subject_id"])
                or record["context_id"] != scope.context_id):
            raise ScopeConflict("feedback can change only this context's own experience")
        if record["revision"] != values["expected_revision"]:
            raise ValueError("posture revision conflict; read the latest experience")
        event = r.causal_ledger.get_event(values["feedback_message_id"])
        trigger = r.causal_ledger.get_event(turn.event_id)
        lineage = r.causal_ledger.committed_history_lineage(scope.world_id, scope.engram_id)
        if (event is None or event.world_id != scope.world_id or event.engram_id not in lineage
                or event.center_id != self._center(scope) or not event.content
                or event.kind is not CausalEventKind.STIMULUS or event.source is not CausalEventSource.USER
                or trigger is None or event.seq is None or trigger.seq is None or event.seq > trigger.seq
                or event.created_at <= datetime.fromisoformat(record["created_at"])):
            raise ScopeConflict("experience feedback requires a later actual user message in this context")
        evidence, redacted, truncated = _source_text(event.content, 6000)
        return {
            "experience_id": record["id"], "expected_revision": values["expected_revision"],
            "verdict": values["verdict"], "evidence": evidence,
            "source": {"kind": "model_interpreted_user_feedback", "world_id": scope.world_id,
                       "experience_record_type": record["record_type"],
                       "feedback_evidence_class": "attributed_user_stimulus_not_independently_verified",
                       "subject_id": scope.engram_id, "context_id": scope.context_id,
                       "feedback_subject_id": event.engram_id,
                       "feedback_message_id": event.id, "learning_turn_id": scope.turn_id,
                       "source_action_id": action_id, "native_request_id": request_id,
                       "arguments_digest": _digest(values), "content_digest": _digest(event.content),
                       "bounded_evidence": redacted or truncated},
        }

    def _recent_sources(self, scope, turn) -> list[dict]:
        r = self.runtime
        trigger = r.causal_ledger.get_event(turn.event_id)
        if trigger is None or trigger.seq is None:
            return []
        lineage = r.causal_ledger.committed_history_lineage(scope.world_id, scope.engram_id)
        placeholders = ",".join("?" for _ in lineage)
        with r.storage._lock:
            rows = r.storage._conn.execute(
                "SELECT id, kind, source, content, seq, engram_id FROM causal_events "
                f"WHERE world_id=? AND engram_id IN ({placeholders}) AND center_id IS ? AND seq<=? "
                "AND ((kind='assistant_result' AND status='settled') "
                "OR (kind='stimulus' AND source='user')) ORDER BY seq DESC LIMIT 8",
                (scope.world_id, *lineage, self._center(scope), trigger.seq),
            ).fetchall()
        return [{"message_id": row[0], "kind": row[1], "source": row[2],
                 "content": bound_text(row[3] or "", 1500)[0], "sequence": row[4], "subject_id": row[5]}
                for row in reversed(rows)]

    @staticmethod
    def _receipt(record) -> dict:
        return {key: record[key] for key in (
            "request_id", "experience_id", "status", "revision", "error_code",
        )} | {"scope": "context", "effect": "experience_not_proven_ability",
             "admission": "after_successful_native_turn"}

    def execute(self, scope, call, cancellation, *, action_request_id=None):
        r = self.runtime
        with self._lock:
            r._require_runtime_owner()
            values = validate(call.arguments)
            r.harness.authorize_posture(scope, action_request_id, call)
            if cancellation.cancelled:
                raise ScopeConflict("posture operation cancelled before admission")
            memory = self._memory()
            turn = self._live_turn(scope)
            if values["op"] == "list":
                from pulse_harness.plugins.posture import pack_posture_materials
                materials = memory.materials(subject_id=scope.engram_id, context_id=scope.context_id)
                packed = pack_posture_materials(materials, byte_budget=16000)
                visible = packed["episodes"][:8]
                return {"experiences": visible,
                        "more_experiences": len(materials["episodes"]) > len(visible),
                        "omitted_experience_count": len(materials["episodes"]) - len(visible),
                        "recent_sources": self._recent_sources(scope, turn),
                        "scope": "current_context_and_explicit_subject_transfers",
                        "semantic_applicability": "not_yet_assessed"}
            if not isinstance(action_request_id, str) or not action_request_id:
                raise ScopeConflict("remember requires its native action identity")
            key = PREFIX + _digest({"scope": scope.as_dict(), "action": action_request_id})
            prior = r.storage.load_component_state(key)
            if prior is not None:
                if prior["arguments_digest"] != _digest(values):
                    raise ScopeConflict("posture replay arguments differ")
                return self._receipt(prior)
            if len(self._requests(pending=True, limit=MAX_PENDING + 1)) >= MAX_PENDING:
                raise ScopeConflict("pending posture capacity reached; finish current turns")
            if values["op"] == "remember":
                experience = self._capture(scope, values, turn, key, action_request_id)
                memory.validate_experience(**experience)
                payload = {"experience": experience, "experience_id": experience["experience_id"]}
            else:
                feedback = self._capture_feedback(scope, values, turn, key, action_request_id)
                payload = {"feedback": feedback, "experience_id": feedback["experience_id"]}
            record = {"version": 1, "request_id": key, "arguments": values,
                      "arguments_digest": _digest(values), "world_id": r.world_id,
                      "subject": scope.engram_id, "source_turn_id": scope.turn_id,
                      "source_action_id": action_request_id, "source_scope": scope.as_dict(),
                      **payload,
                      "status": "prepared", "revision": None, "error_code": None}
            r.storage.save_component_state(key, record)
            return self._receipt(record)

    def _finish(self, record, status, error=None, revision=None):
        self.runtime.storage.save_component_state(record["request_id"], {
            **record, "status": status, "error_code": error, "revision": revision,
        })

    def cancel_subject(self, subject) -> int:
        with self._lock:
            pending = self._requests(pending=True, subject=subject, limit=MAX_PENDING)
            cancelled = 0
            for record in pending:
                source = self.runtime.causal_ledger.get_turn_for_world(
                    record["source_turn_id"], self.runtime.world_id,
                )
                # A later stop does not cancel an earlier completed turn's
                # learning. Drain still verifies its exact native action.
                if source is not None and source.state is HarnessTurnState.SETTLED and source.result_event_id:
                    continue
                self._finish(record, "cancelled", "subject_stopped")
                cancelled += 1
            return cancelled

    def _has_active_continuation(self, subject_id: str) -> bool:
        """Accept completed work across a unique, committed World succession."""
        r = self.runtime
        seen = set()
        with r.storage._lock:
            for _ in range(256):
                if subject_id in seen:
                    return False
                seen.add(subject_id)
                subject = r.storage.get_engram(subject_id)
                if subject is None:
                    return False
                if subject.status is EngramStatus.ACTIVE:
                    return True
                if subject.status is not EngramStatus.ARCHIVED:
                    return False
                successors = r.storage._conn.execute(
                    "SELECT g.successor_id FROM generation_transitions g "
                    "JOIN causal_events e ON e.id=g.event_id "
                    "WHERE e.world_id=? AND g.predecessor_id=? AND g.state='committed' LIMIT 2",
                    (r.world_id, subject_id),
                ).fetchall()
                if len(successors) != 1 or not successors[0][0]:
                    return False
                subject_id = successors[0][0]
        return False

    def drain(self) -> None:
        """Admit successful experiences during the existing Runtime tick."""
        r = self.runtime
        with self._lock:
            r._require_runtime_owner()
            memory = self._memory()
            for record in self._requests(pending=True, limit=MAX_PENDING):
                source = r.causal_ledger.get_turn_for_world(record["source_turn_id"], r.world_id)
                if (source is None or record["world_id"] != r.world_id
                        or source.engram_id != record["subject"]):
                    self._finish(record, "blocked", "source_unavailable")
                    continue
                if source.state is HarnessTurnState.RUNNING:
                    continue
                if source.state is not HarnessTurnState.SETTLED or not source.result_event_id:
                    self._finish(record, "cancelled", "source_did_not_settle")
                    continue
                scope = HarnessScope.from_dict(record["source_scope"])
                try:
                    action = r.kernel_service.store.get_action(scope, record["source_action_id"])
                    turn = r.kernel_service.store.get_turn(scope)
                except ScopeConflict:
                    self._finish(record, "blocked", "native_source_unavailable")
                    continue
                if (turn.status is not TurnStatus.SUCCEEDED or action.status is not ActionStatus.SUCCEEDED
                        or action.tool_name != NAME or dict(action.arguments) != record["arguments"]
                        or _digest(record["arguments"]) != record["arguments_digest"]
                        or r._native_kernel_context_for_turn(record["subject"], source.id) != scope.context_id):
                    self._finish(record, "cancelled", "native_action_not_settled")
                    continue
                if not self._has_active_continuation(record["subject"]):
                    self._finish(record, "cancelled", "subject_inactive")
                    continue
                try:
                    if record["arguments"]["op"] == "feedback":
                        saved = memory.get_experience(record["experience_id"])
                        prior = [entry for entry in saved["feedback"]
                                 if entry["source"].get("native_request_id") == record["request_id"]]
                        if prior:
                            if (len(prior) != 1
                                    or prior[0]["source"] != record["feedback"]["source"]
                                    or prior[0]["verdict"] != record["feedback"]["verdict"]
                                    or prior[0]["evidence"] != record["feedback"]["evidence"]):
                                self._finish(record, "blocked", "feedback_identity_conflict")
                                continue
                        else:
                            if (not memory.owns_subject(record["subject"], saved["subject_id"])
                                    or saved["context_id"] != scope.context_id):
                                self._finish(record, "blocked", "experience_scope_changed")
                                continue
                            saved = memory.record_feedback(**record["feedback"])
                        self._finish(record, "feedback_recorded", revision=saved["revision"])
                    else:
                        try:
                            saved = memory.get_experience(record["experience_id"])
                        except ValueError:
                            saved = memory.add_experience(**record["experience"])
                        if (saved["subject_id"] != record["subject"]
                                or saved["source"] != record["experience"]["source"]):
                            self._finish(record, "blocked", "experience_identity_conflict")
                            continue
                        self._finish(record, "remembered", revision=saved["revision"])
                except ValueError:
                    self._finish(record, "blocked", "experience_not_admitted")
