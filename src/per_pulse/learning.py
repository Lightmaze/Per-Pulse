"""Page-scoped management of attributed posture experience.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
The World owns the journal. This HTTP projection cannot invent a native turn,
source message, user correction, or evidence of improved model behavior.
"""
from __future__ import annotations

from copy import deepcopy
from pulse_harness.core.errors import RedactionRequired, ScopeConflict

from .service import PageError


class PageLearning:
    def __init__(self, pages):
        self.pages = pages

    @property
    def enabled(self):
        return self.pages.runtime.harness.posture_memory is not None

    def _memory(self):
        memory = self.pages.runtime.harness.posture_memory
        if memory is None:
            raise PageError("posture_disabled", "Start Per Pulse with --posture to use experience learning.", 409)
        return memory

    def _context(self, page_id):
        front = self.pages._front(page_id)
        return "work:" + front.center_id if front else ""

    def _subject(self, page_id):
        front = self.pages._front(page_id)
        return front.focal_engram_id if front else self.pages.runtime.continuity_engram_id

    def _owned(self, page_id, experience_id):
        memory = self._memory()
        context_id = self._context(page_id)
        try:
            record = memory.get_experience(experience_id)
        except ValueError:
            raise PageError("experience_not_found", "This lesson does not exist.", 404) from None
        if (not memory.owns_subject(self._subject(page_id), record["subject_id"])
                or record["context_id"] != context_id):
            raise PageError("experience_not_found", "Edit this lesson from its source page.", 404)
        return memory, record

    @staticmethod
    def _invalid(error):
        if isinstance(error, RedactionRequired):
            return PageError("invalid_experience", "A credential cannot be saved as a lesson. Remove it and try again.", 422)
        message = str(error)
        if "revision conflict" in message:
            return PageError("experience_revision_conflict", "This lesson changed. Refresh it before saving.", 409)
        return PageError("invalid_experience", message, 422)

    def _public(self, record, context_id):
        local = record["context_id"] == context_id
        result = {key: deepcopy(record[key]) for key in (
            "id", "revision", "title", "lesson", "conditions", "counterexamples",
            "shift", "scope", "active", "record_type", "created_at", "updated_at",
        )}
        source_context = record["context_id"]
        source_page = "home" if not source_context else None
        if source_context.startswith("work:"):
            source_page = next((front["id"] for front in self.pages.runtime.list_task_fronts()
                                if front["center_id"] == source_context.removeprefix("work:")), None)
        result.update(canEdit=local, canReport=local or (record["scope"] == "subject" and record["active"]), sourcePageId=source_page)
        # Shared experiences expose only the original message anchors so the
        # reader can inspect their provenance without copying the raw episode.
        source = record.get("source", {})
        if source_page is not None and isinstance(source, dict):
            original, correction = source.get("source_message_id"), source.get("feedback_message_id")
            if all(isinstance(value, str) and value for value in (original, correction)):
                result["sourceAnchor"] = {"resultMessageId": original, "feedbackMessageId": correction}
        # Cross-page transfer contains the abstraction only. The original
        # encounter and raw feedback stay behind navigation to its source.
        if local:
            result["encounter"] = deepcopy(record["encounter"])
            result["source"] = deepcopy(record["source"])
            result["feedback"] = deepcopy(record["feedback"])
        return result

    def view(self, page_id):
        with self.pages._lock:
            context_id = self._context(page_id)
            if not self.enabled:
                return {"enabled": False, "experiences": []}
            records = self._memory().list_experiences(
                subject_id=self._subject(page_id),
                context_id=context_id, include_inactive=True,
            )
            return {"enabled": True, "experiences": [self._public(r, context_id) for r in records]}

    def create(self, page_id, data):
        from pulse_system.service.native_posture import capture_encounter

        with self.pages._lock:
            memory = self._memory()
            context_id = self._context(page_id)
            subject_id = self._subject(page_id)
            self.pages._check_message(page_id, data["sourceMessageId"])
            self.pages._check_message(page_id, data["feedbackMessageId"])
            try:
                captured = capture_encounter(
                    self.pages.runtime, subject_id, context_id,
                    data["sourceMessageId"], data["feedbackMessageId"],
                )
                record = memory.add_experience(
                    subject_id=subject_id, context_id=context_id,
                    **{key: data[key] for key in ("title", "lesson", "conditions", "counterexamples", "shift", "scope")},
                    encounter=captured["encounter"],
                    source={**captured["source"], "capture_kind": "operator_selected_correction"},
                    record_type=captured["record_type"],
                )
            except (ValueError, RedactionRequired) as error:
                raise self._invalid(error) from error
            except ScopeConflict as error:
                raise PageError("invalid_experience_source", str(error), 422) from error
            return {"experience": self._public(record, context_id)}

    def revise(self, page_id, experience_id, revision, changes, evidence=None):
        with self.pages._lock:
            memory, record = self._owned(page_id, experience_id)
            # A described correction may alter the lesson, not silently change
            # scope or activation. The report is not invented native feedback.
            if evidence is not None and (
                not isinstance(changes, dict) or not changes
                or set(changes) - {"title", "lesson", "conditions", "counterexamples", "shift"}
            ):
                raise PageError("invalid_experience", "A sourced refinement can only change lesson content.", 422)
            try:
                if evidence is None:
                    updated = memory.revise_experience(
                        experience_id, expected_revision=revision, changes=changes,
                    )
                else:
                    updated = memory.revise_from_feedback(
                        experience_id, expected_revision=revision, changes=changes,
                        evidence=evidence,
                        source={"kind": "operator_described_refinement", "page_id": page_id,
                                "experience_id": experience_id},
                    )
            except (ValueError, RedactionRequired) as error:
                raise self._invalid(error) from error
            return {"experience": self._public(updated, record["context_id"])}

    def feedback(self, page_id, experience_id, revision, verdict, evidence):
        """Let a receiving Work report an outcome of shared guidance.

        Editing and sharing remain source-Work-only. A recipient can only rate
        an active, explicitly shared lesson of the same continuing subject.
        The rating is attributed operator testimony, not a verified native
        result or a claim that the lesson caused the outcome.
        """
        with self.pages._lock:
            memory = self._memory()
            context_id = self._context(page_id)
            try:
                record = memory.get_experience(experience_id)
            except ValueError:
                raise PageError("experience_not_found", "This lesson does not exist.", 404) from None
            local = record["context_id"] == context_id
            if (not memory.owns_subject(self._subject(page_id), record["subject_id"])
                    or not local and (record["scope"] != "subject" or not record["active"])):
                raise PageError("experience_not_found",
                                "This lesson is not available for feedback from this page.", 404)
            try:
                updated = memory.record_feedback(
                    experience_id, expected_revision=revision, verdict=verdict, evidence=evidence,
                    source={"kind": "operator_report", "page_id": page_id,
                            "relation": "source" if local else "shared_recipient"},
                )
            except (ValueError, RedactionRequired) as error:
                raise self._invalid(error) from error
            # Never return source-only encounter or private feedback to a
            # receiving Work, including after a harmful rating pauses sharing.
            return {"experience": self._public(updated, context_id)}
