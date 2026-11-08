"""Attributed posture experience in the existing World's component state.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

This is a revisable experience journal, not a second conversation store or a
semantic classifier. Scope and lifecycle are enforced here; a configured model
interprets the conditions and counterexamples against the current task.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
import re
from typing import Any
from uuid import uuid4



SCHEMA = "per-pulse.posture.v2"
LEGACY_SCHEMA = "per-pulse.posture.v1"
CONTROL_SCHEMA = "per-pulse.posture-controls.v1"
LEGACY_MAX_STOP_CONTROLS = 128
_LESSON_FIELDS = ("title", "lesson", "conditions", "counterexamples", "shift")
_EXAMPLE_FIELDS = ("title", "excerpt", "note")
_EDITABLE = frozenset({*_LESSON_FIELDS, "note", "scope", "active"})
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
        size = len(value.encode("utf-8"))
    except (UnicodeError, TypeError) as exc:
        raise ValueError(f"{name} must be valid Unicode text") from exc
    if size > limit:
        raise ValueError(f"{name} is too large")
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
    if value.get("representation", "lesson") == "example":
        _text(value.get("excerpt"), "example excerpt", 8000)
        note = value.get("note")
        if not isinstance(note, str):
            raise ValueError("example note must be text")
        if note:
            _text(note, "example note", 4000)
    else:
        if value.get("representation", "lesson") != "lesson":
            raise ValueError("unknown experience representation")
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


def experience_content_fields(record: dict) -> tuple[str, ...]:
    """Only intentionally transferable content, never the private episode."""
    representation = record.get("representation", "lesson")
    if representation == "example":
        return _EXAMPLE_FIELDS
    if representation == "lesson":
        return _LESSON_FIELDS
    raise ValueError("unknown experience representation")


def experience_search_text(record: dict) -> str:
    return "\n".join(value if isinstance(value := record[key], str) else
                     json.dumps(value, ensure_ascii=False)
                     for key in experience_content_fields(record))


def _check_changes(record: dict, changes: dict) -> None:
    # An example's exact passage and representation are immutable. A later
    # explanation can change without rewriting what the earlier result was.
    allowed = set(experience_content_fields(record)) - {"excerpt"} | {"scope", "active"}
    if set(changes) - allowed:
        raise ValueError("revision fields do not belong to this experience representation")


class PostureMemory:
    """One world's durable experience journal, sharing Storage's transactions.

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
        from .posture_journal import PostureJournal
        self._journal = PostureJournal(storage, world_id, legacy=self._legacy_state,
                                       guard=guard, content_time=self._content_revision_time,
                                       content_fields=experience_content_fields)

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

    def _legacy_state(self) -> dict:
        state = self.storage.load_component_state(self.component)
        if state is None:
            state = {"schema": SCHEMA, "world_id": self.world_id, "experiences": {}}
        if state.get("schema") not in {SCHEMA, LEGACY_SCHEMA} or state.get("world_id") != self.world_id:
            raise ValueError("unsupported or mismatched posture state")
        if state["schema"] == LEGACY_SCHEMA:
            if any(record.get("representation", "lesson") != "lesson" for record in state["experiences"].values()):
                raise ValueError("legacy posture state contains an unsupported representation")
            # Read old lessons in place. The next ordinary mutation imports
            # this view into World journal rows, preserving IDs and history.
            state["schema"] = SCHEMA
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
                or not isinstance(controls.get("stops"), dict) or len(controls["stops"]) > LEGACY_MAX_STOP_CONTROLS):
            raise ValueError("unsupported or mismatched posture stop controls")
        return controls

    @staticmethod
    def _record(state, experience_id) -> dict:
        if experience_id not in state["experiences"]:
            raise ValueError("unknown posture experience")
        return state["experiences"][experience_id]

    @staticmethod
    def _advance(record: dict) -> None:
        previous = {key: deepcopy(value) for key, value in record.items() if key != "history"}
        record["history"].append(previous)
        record.pop("revision_source", None)
        record["revision"] += 1
        record["updated_at"] = datetime.now(timezone.utc).isoformat()

    @staticmethod
    def validate_experience(
        *, subject_id: str, context_id: str, title: str, lesson: str = "",
        encounter: dict, conditions: Any = None, counterexamples: list[str] | None = None,
        shift: dict | None = None,
        source: dict, scope: str = "context", record_type: str = "observed_episode",
        experience_id: str | None = None,
        representation: str = "lesson", excerpt: str | None = None, note: str = "",
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
        allowed_encounters = [{"situation", "action", "outcome"}]
        if representation == "example":
            allowed_encounters.append({"situation", "action"})
        if not isinstance(encounter, dict) or set(encounter) not in allowed_encounters:
            raise ValueError("encounter requires situation, action, and outcome")
        for key, value in encounter.items():
            _text(value, "encounter " + key, 12000)
        now = datetime.now(timezone.utc).isoformat()
        record = {
            "id": experience_id, "revision": 1, "subject_id": subject_id,
            "context_id": context_id, "title": title, "representation": representation,
            "encounter": deepcopy(encounter),
            "source": _source(source), "scope": scope, "record_type": record_type,
            "active": True, "created_at": now, "updated_at": now,
            "feedback": [], "history": [],
        }
        if representation == "example":
            if lesson or conditions is not None or counterexamples is not None or shift is not None:
                raise ValueError("a concrete example does not require lesson fields")
            record.update(excerpt=excerpt, note=note)
        else:
            if excerpt is not None or note:
                raise ValueError("example fields cannot be attached to a lesson")
            record.update(lesson=lesson, conditions=deepcopy(conditions),
                          counterexamples=deepcopy(counterexamples), shift=deepcopy(shift))
        _validate_content(record)
        return record

    def add_experience(self, **kwargs) -> dict:
        record = self.validate_experience(**kwargs)
        self.guard()
        return self._journal.add(record)

    def get_experience(self, experience_id: str) -> dict:
        self.guard()
        return self._journal.get(experience_id)

    def list_experiences(self, *, subject_id: str, context_id: str | None = None,
                         include_inactive: bool = False) -> list[dict]:
        self.guard()
        records = [record for record, _ in self._journal.list(subjects=self._subjects(subject_id),
                   context_id=context_id, include_inactive=include_inactive)]
        return sorted(records, key=lambda value: (value["updated_at"], value["id"]), reverse=True)

    def revise_experience(self, experience_id: str, *, expected_revision: int,
                          changes: dict, revision_source: dict | None = None) -> dict:
        """Revise content or controls, retaining an optional host-attributed actor.

        A native interpretation changes only an example's title or note. Its
        actor belongs to this revision and its history, separate from the
        original observation and from outcome feedback.
        """
        if not isinstance(changes, dict) or not changes or set(changes) - _EDITABLE:
            raise ValueError("posture revision contains unsupported fields")
        if revision_source is not None:
            revision_source = _source(revision_source)
        def update(record):
            _check_changes(record, changes)
            if revision_source is not None and (record.get("representation") != "example"
                                                or set(changes) - {"title", "note"}):
                raise ValueError("an attributed interpretation can change only an example title or note")
            self._advance(record)
            record.update(deepcopy(changes))
            if revision_source is not None:
                record["revision_source"] = revision_source
            _validate_content(record)
        self.guard()
        return self._journal.revise(experience_id, expected_revision, update)

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
        def update(record):
            _check_changes(record, changes)
            self._advance(record)
            record.update(deepcopy(changes))
            _validate_content(record)
            record["feedback"].append({
                "id": "feedback-" + uuid4().hex, "revision": expected_revision,
                "verdict": "revised", "evidence": evidence, "source": source,
                "created_at": record["updated_at"],
            })
        self.guard()
        return self._journal.revise(experience_id, expected_revision, update)

    def record_feedback(self, experience_id: str, *, expected_revision: int,
                        verdict: str, evidence: str, source: dict | None = None) -> dict:
        if not isinstance(verdict, str) or verdict not in _VERDICTS:
            raise ValueError("invalid posture feedback verdict")
        _text(evidence, "feedback evidence", 6000)
        if source is not None:
            source = _source(source)
        def update(record):
            self._advance(record)
            record["feedback"].append({
                "id": "feedback-" + uuid4().hex, "revision": expected_revision,
                "verdict": verdict, "evidence": evidence,
                "source": source or {"kind": "operator_report"},
                "created_at": record["updated_at"],
            })
            if verdict in {"harmful", "withdrawn"}:
                record["active"] = False
        self.guard()
        return self._journal.revise(experience_id, expected_revision, update)

    @staticmethod
    def _content_revision_time(record: dict) -> str:
        """Do not make a feedback-only update appear like a newly learned rule.

        The journal itself stays newest-update-first. For limited automatic
        work material, use the last change to content or eligibility. A report
        is still included as evidence, but does not acquire attention priority
        merely by being recent. Legacy imports derive this time from snapshots;
        subsequent journal writes retain it alongside the current record.
        """
        fields = (*experience_content_fields(record), "scope", "active")
        content_version = record
        for previous in reversed(record.get("history", [])):
            if any(previous.get(key) != record.get(key) for key in fields):
                break
            content_version = previous
        return content_version["updated_at"]

    def materials(self, *, subject_id: str, context_id: str,
                  prioritize_content: bool = False) -> dict:
        """Offer eligible experiences, not allegedly relevant experiences.

        Context privacy is a hard boundary. Explicit subject transfer offers
        the lesson abstraction or explicitly shared example passage and note,
        never the originating context's full encounter or private feedback.

        Native list/find keep their newest-journal-update order. Only the
        optional model work packet prioritizes the last content or eligibility
        edit, so an unchanged lesson's new feedback does not displace newer
        candidates under the bounded prompt budget.
        """
        self.guard()
        rows = self._journal.list(subjects=self._subjects(subject_id), context_id=context_id,
                                  include_inactive=False, history=False)
        rows.sort(key=lambda item: (item[1] if prioritize_content else item[0]["updated_at"],
                                   item[0]["id"]), reverse=True)
        records = [record for record, _ in rows]
        episodes = []
        for record in records:
            local = record["context_id"] == context_id
            item = {key: deepcopy(record[key]) for key in (
                "id", "revision", "record_type", "scope", "context_id",
                *experience_content_fields(record),
            )}
            item["representation"] = record.get("representation", "lesson")
            item["source"] = {"experience_id": record["id"], "revision": record["revision"],
                              "context_id": record["context_id"]}
            if record["source"].get("kind") == "native_reading_example":
                item["source"]["kind"] = "native_reading_example"
                item["source"]["meaning"] = "retained_reading_observation_not_verified_source_or_outcome"
            if record["source"].get("kind") == "native_command_example":
                item["source"].update(kind="native_command_example",
                                      meaning="retained_command_observation_not_quality_rating",
                                      command_observation=deepcopy(record["source"]["command_observation"]))
            if record["source"].get("execution_regime") == "replay":
                # Share the meaning, not the original page's selected sources.
                item["source"]["execution_regime"] = "replay"
                item["source"]["meaning"] = "replay_interpretation_not_new_external_observation"
            item["encounter_available"] = local
            if local:
                item["encounter"] = deepcopy(record["encounter"])
                item["source"]["evidence"] = deepcopy(record["source"])
                item["feedback"] = deepcopy(record["feedback"][-4:])
                if "revision_source" in record:
                    item["revision_source"] = deepcopy(record["revision_source"])
            else:
                item["feedback"] = [{"verdict": entry["verdict"], "revision": entry["revision"]}
                                    for entry in record["feedback"][-4:]]
                if "revision_source" in record:
                    item["revision_source"] = {key: record["revision_source"][key]
                                               for key in ("kind", "selector_evidence_class")
                                               if key in record["revision_source"]}
            episodes.append(item)
        return {"schema": SCHEMA, "episodes": episodes, "eligibility": "subject_scope_and_active_only",
                "semantic_applicability": "not_yet_assessed", "source_context_id": context_id}
