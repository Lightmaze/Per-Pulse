"""Attributed posture experience in the existing World's component state.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

This is a revisable experience journal, not a second conversation store or a
semantic classifier. Scope and lifecycle are enforced here; a configured model
interprets the conditions and counterexamples against the current task.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import re
from typing import Any
from uuid import uuid4

from pulse_harness.core.replay import bound_text


SCHEMA = "per-pulse.posture.v1"
CONTROL_SCHEMA = "per-pulse.posture-controls.v1"
MAX_EXPERIENCES = 128
MAX_STATE_BYTES = 4 * 1024 * 1024
# 128 * (6000 evidence bytes * 6 worst-case JSON escaping + 8192 source
# bytes + 1024 identity/transition metadata) + 4096 world/schema bytes fits.
# The source bound already includes its canonical JSON escaping.
MAX_CONTROL_STATE_BYTES = 6 * 1024 * 1024
_EDITABLE = frozenset({"title", "lesson", "conditions", "counterexamples", "shift", "scope", "active"})
_LEARNED_EDITABLE = _EDITABLE - {"scope", "active"}
_VERDICTS = frozenset({"useful", "not_useful", "harmful", "withdrawn"})


def posture_enabled() -> bool:
    value = os.environ.get("PER_PULSE_POSTURE", "0")
    if value not in {"0", "1"}:
        raise ValueError("PER_PULSE_POSTURE must be 0 or 1")
    return value == "1"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    try:
        clean, redacted, truncated = bound_text(value, limit)
    except (UnicodeError, TypeError) as exc:
        raise ValueError(f"{name} must be valid Unicode text") from exc
    if redacted or truncated or clean != value:
        raise ValueError(f"{name} is too large or contains a credential")
    return value


def _source(value: Any) -> dict:
    if not isinstance(value, dict) or not value:
        raise ValueError("source must contain attributed evidence")
    _text(_canonical(value), "source", 8192)
    return deepcopy(value)


def _conditions(value: Any) -> Any:
    """Keep conjunctive/disjunctive boundaries explicit, without lexical tests."""
    nodes = 0

    def visit(item, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > 32 or depth > 6:
            raise ValueError("conditions exceed their structural bound")
        if isinstance(item, str):
            return _text(item, "condition", 1000)
        if not isinstance(item, dict) or len(item) != 1:
            raise ValueError("a condition is text, all, any, or not")
        key, children = next(iter(item.items()))
        if key == "not":
            return {key: visit(children, depth + 1)}
        if key not in {"all", "any"} or not isinstance(children, list) or not 1 <= len(children) <= 12:
            raise ValueError("all/any require a nonempty bounded list")
        return {key: [visit(child, depth + 1) for child in children]}

    result = visit(value)
    _text(_canonical(result), "conditions", 8192)
    return result


def _validate_content(value: dict) -> None:
    _text(value.get("title"), "title", 240)
    _text(value.get("lesson"), "lesson", 4000)
    value["conditions"] = _conditions(value.get("conditions"))
    counterexamples = value.get("counterexamples")
    if not isinstance(counterexamples, list) or not 1 <= len(counterexamples) <= 8:
        raise ValueError("provide 1..8 concrete counterexamples or boundaries")
    for item in counterexamples:
        _text(item, "counterexample", 2000)
    shift = value.get("shift")
    if not isinstance(shift, dict) or set(shift) != {"understanding", "candidates", "delivery"}:
        raise ValueError("shift requires understanding, candidates, and delivery")
    for key, item in shift.items():
        _text(item, key, 2400)
    if not isinstance(value.get("scope"), str) or value["scope"] not in {"context", "subject"}:
        raise ValueError("scope must be context or subject")
    if type(value.get("active")) is not bool:
        raise ValueError("active must be a boolean")


class PostureMemory:
    """One world's bounded experience journal, sharing Storage's write lock.

    The host validates that ``source`` actually belongs to an observed native
    turn before recording an observed episode. This class preserves that
    attribution; it never manufactures external feedback from a model summary.
    All updates use a required expected revision and retain the old revision.
    """

    def __init__(self, storage, world_id: str, *, guard=lambda: None, history_lineage=None):
        self.storage = storage
        self.world_id = _text(world_id, "world_id", 512)
        self.guard = guard
        # Only the owning runtime supplies this authority. It is never read
        # from experience text, a provider response, or an API parameter.
        self.history_lineage = history_lineage
        self.component = f"per-pulse-posture-v1:{world_id}"
        self.control_component = f"per-pulse-posture-controls-v1:{world_id}"

    def _subjects(self, subject_id: str) -> set[str]:
        _text(subject_id, "subject_id", 512)
        if self.history_lineage is None:
            return {subject_id}
        lineage = self.history_lineage(subject_id)
        if (not isinstance(lineage, (tuple, list)) or len(lineage) > 1024
                or any(not isinstance(value, str) or not value for value in lineage)):
            raise ValueError("invalid host subject lineage")
        return {subject_id, *lineage}

    def owns_subject(self, current_subject_id: str, record_subject_id: str) -> bool:
        """Authorize current or ancestor experience using host-owned lineage."""
        self.guard()
        return record_subject_id in self._subjects(current_subject_id)

    @contextmanager
    def _locked(self):
        self.guard()
        with self.storage._lock:
            self.guard()
            yield

    def _load(self) -> dict:
        state = self.storage.load_component_state(self.component)
        if state is None:
            state = {"schema": SCHEMA, "world_id": self.world_id, "experiences": {}}
        if state.get("schema") != SCHEMA or state.get("world_id") != self.world_id:
            raise ValueError("unsupported or mismatched posture state")
        controls = self._load_controls()
        for identity, control in controls["stops"].items():
            record = self._record(state, identity)
            if record["revision"] >= control["revision"]:
                # A later ordinary revision has already absorbed this control
                # and its full history. An old stop cannot undo reactivation.
                continue
            if (record["revision"] != control["base_revision"]
                    or control["revision"] != control["base_revision"] + 1
                    or not record["active"]):
                raise ValueError("posture stop control does not match its base revision")
            # Reconstruct the same complete previous snapshot as an ordinary
            # update, without duplicating it in a physically full journal.
            self._advance(record)
            record["updated_at"] = control["updated_at"]
            record["active"] = False
            if control["feedback"] is not None:
                record["feedback"].append(deepcopy(control["feedback"]))
        return state

    def _load_controls(self) -> dict:
        controls = self.storage.load_component_state(self.control_component)
        if controls is None:
            return {"schema": CONTROL_SCHEMA, "world_id": self.world_id, "stops": {}}
        if (controls.get("schema") != CONTROL_SCHEMA or controls.get("world_id") != self.world_id
                or not isinstance(controls.get("stops"), dict) or len(controls["stops"]) > MAX_EXPERIENCES):
            raise ValueError("unsupported or mismatched posture stop controls")
        return controls

    def _save(self, state: dict, *, stop_control: dict | None = None) -> None:
        if len(_canonical(state).encode()) > MAX_STATE_BYTES:
            if stop_control is None:
                raise ValueError("posture journal exceeds its bound; no update was saved")
            # Disabling must remain possible at capacity. This bounded control
            # component retains one transition per experience, with its complete
            # new negative evidence. Prior history stays in the original World
            # component. It is merged by revision, never silently discarded.
            controls = self._load_controls()
            controls["stops"][stop_control["id"]] = stop_control
            if (len(controls["stops"]) > MAX_EXPERIENCES
                    or len(_canonical(controls).encode()) > MAX_CONTROL_STATE_BYTES):
                raise ValueError("posture stop controls exceed their bound; no update was saved")
            self.guard()
            self.storage.save_component_state(self.control_component, controls)
            return
        self.guard()
        self.storage.save_component_state(self.component, state)

    @staticmethod
    def _stop_control(record: dict, *, feedback: dict | None = None) -> dict:
        return {"id": record["id"], "base_revision": record["revision"] - 1,
                "revision": record["revision"], "updated_at": record["updated_at"],
                "feedback": deepcopy(feedback)}

    @staticmethod
    def _record(state, experience_id) -> dict:
        if experience_id not in state["experiences"]:
            raise ValueError("unknown posture experience")
        return state["experiences"][experience_id]

    @staticmethod
    def _revision(record: dict, expected_revision: int) -> None:
        if type(expected_revision) is not int or record["revision"] != expected_revision:
            raise ValueError("posture revision conflict; read the latest experience")

    @staticmethod
    def _advance(record: dict) -> None:
        previous = {key: deepcopy(value) for key, value in record.items() if key != "history"}
        record["history"].append(previous)
        record["revision"] += 1
        record["updated_at"] = datetime.now(timezone.utc).isoformat()

    @staticmethod
    def validate_experience(
        *, subject_id: str, context_id: str, title: str, lesson: str,
        encounter: dict, conditions: Any, counterexamples: list[str], shift: dict,
        source: dict, scope: str = "context", record_type: str = "observed_episode",
        experience_id: str | None = None,
    ) -> dict:
        subject_id = _text(subject_id, "subject_id", 512)
        # The native Harness identifies Home with an empty context id.
        if not isinstance(context_id, str):
            raise ValueError("context_id must be text")
        if context_id:
            _text(context_id, "context_id", 512)
        experience_id = experience_id or "posture-" + uuid4().hex
        if not isinstance(experience_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}", experience_id):
            raise ValueError("invalid posture experience identity")
        if not isinstance(record_type, str) or record_type not in {"observed_episode", "constructed_fixture"}:
            raise ValueError("invalid posture record_type")
        if not isinstance(encounter, dict) or set(encounter) != {"situation", "action", "outcome"}:
            raise ValueError("encounter requires situation, action, and outcome")
        for key, value in encounter.items():
            _text(value, "encounter " + key, 12000)
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "id": experience_id, "revision": 1, "subject_id": subject_id,
            "context_id": context_id, "title": title, "lesson": lesson,
            "encounter": deepcopy(encounter), "conditions": deepcopy(conditions),
            "counterexamples": deepcopy(counterexamples), "shift": deepcopy(shift),
            "source": _source(source), "scope": scope, "record_type": record_type,
            "active": True, "created_at": now, "updated_at": now,
            "feedback": [], "history": [],
        }
        _validate_content(record)
        return record

    def add_experience(self, **kwargs) -> dict:
        record = self.validate_experience(**kwargs)
        experience_id = record["id"]
        with self._locked():
            state = self._load()
            if experience_id in state["experiences"]:
                raise ValueError("posture identity already exists; revise it explicitly")
            if len(state["experiences"]) >= MAX_EXPERIENCES:
                raise ValueError("posture journal has reached its experience bound")
            state["experiences"][experience_id] = record
            self._save(state)
        return deepcopy(record)

    def get_experience(self, experience_id: str) -> dict:
        with self._locked():
            return deepcopy(self._record(self._load(), experience_id))

    def list_experiences(self, *, subject_id: str, context_id: str | None = None,
                         include_inactive: bool = False) -> list[dict]:
        with self._locked():
            subjects = self._subjects(subject_id)
            values = self._load()["experiences"].values()
            records = [deepcopy(value) for value in values
                       if value["subject_id"] in subjects
                       and (include_inactive or value["active"])
                       and (context_id is None or value["context_id"] == context_id
                            or value["scope"] == "subject")]
        return sorted(records, key=lambda value: (value["updated_at"], value["id"]), reverse=True)

    def revise_experience(self, experience_id: str, *, expected_revision: int,
                          changes: dict) -> dict:
        if not isinstance(changes, dict) or not changes or set(changes) - _EDITABLE:
            raise ValueError("posture revision contains unsupported fields")
        with self._locked():
            state = self._load()
            record = self._record(state, experience_id)
            self._revision(record, expected_revision)
            was_active = record["active"]
            self._advance(record)
            record.update(deepcopy(changes))
            _validate_content(record)
            control = self._stop_control(record) if was_active and changes == {"active": False} else None
            self._save(state, stop_control=control)
            return deepcopy(record)

    def revise_from_feedback(self, experience_id: str, *, expected_revision: int,
                             changes: dict, evidence: str, source: dict) -> dict:
        """Refine a lesson and attribute its correction in one journal write.

        The native host establishes the later user-message and action
        provenance. A learned revision cannot share or reactivate a lesson;
        those controls remain with the operator. ``revised`` records a change,
        not a positive usefulness judgment.
        """
        if not isinstance(changes, dict) or not changes or set(changes) - _LEARNED_EDITABLE:
            raise ValueError("learned revision contains unsupported fields")
        _text(evidence, "revision evidence", 6000)
        source = _source(source)
        with self._locked():
            state = self._load()
            record = self._record(state, experience_id)
            self._revision(record, expected_revision)
            self._advance(record)
            record.update(deepcopy(changes))
            _validate_content(record)
            record["feedback"].append({
                "id": "feedback-" + uuid4().hex, "revision": expected_revision,
                "verdict": "revised", "evidence": evidence, "source": source,
                "created_at": record["updated_at"],
            })
            self._save(state)
            return deepcopy(record)

    def record_feedback(self, experience_id: str, *, expected_revision: int,
                        verdict: str, evidence: str, source: dict | None = None) -> dict:
        if not isinstance(verdict, str) or verdict not in _VERDICTS:
            raise ValueError("invalid posture feedback verdict")
        _text(evidence, "feedback evidence", 6000)
        if source is not None:
            source = _source(source)
        with self._locked():
            state = self._load()
            record = self._record(state, experience_id)
            self._revision(record, expected_revision)
            was_active = record["active"]
            self._advance(record)
            record["feedback"].append({
                "id": "feedback-" + uuid4().hex, "revision": expected_revision,
                "verdict": verdict, "evidence": evidence,
                "source": source or {"kind": "operator_report"},
                "created_at": record["updated_at"],
            })
            if verdict in {"harmful", "withdrawn"}:
                record["active"] = False
            control = (self._stop_control(record, feedback=record["feedback"][-1])
                       if was_active and verdict in {"harmful", "withdrawn"} else None)
            self._save(state, stop_control=control)
            return deepcopy(record)

    def materials(self, *, subject_id: str, context_id: str) -> dict:
        """Offer eligible experiences, not allegedly relevant experiences.

        Context privacy is a hard boundary. Explicit subject transfer offers
        the revisable abstraction and source locators, never the originating
        context's encounter text, raw feedback, or previous conversation.
        """
        records = self.list_experiences(subject_id=subject_id, context_id=context_id)
        episodes = []
        for record in records:
            local = record["context_id"] == context_id
            item = {key: deepcopy(record[key]) for key in (
                "id", "revision", "title", "lesson", "conditions", "counterexamples",
                "shift", "record_type", "scope", "context_id",
            )}
            item["source"] = {"experience_id": record["id"], "revision": record["revision"],
                              "context_id": record["context_id"]}
            item["encounter_available"] = local
            if local:
                item["encounter"] = deepcopy(record["encounter"])
                item["source"]["evidence"] = deepcopy(record["source"])
                item["feedback"] = deepcopy(record["feedback"][-4:])
            else:
                item["feedback"] = [{"verdict": entry["verdict"], "revision": entry["revision"]}
                                    for entry in record["feedback"][-4:]]
            episodes.append(item)
        return {"schema": SCHEMA, "episodes": episodes, "eligibility": "subject_scope_and_active_only",
                "semantic_applicability": "not_yet_assessed", "source_context_id": context_id}
