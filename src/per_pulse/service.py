"""Session pages as a projection of native Home/Work and causal events.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.

This adapter owns no model loop and no conversation store. Message IDs are
native causal-event IDs, Work IDs are native TaskFront IDs, and links live in
the same World database's component state. The Kernel remains the sole turn
runner. Reading or linking a page never copies another page's transcript.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import threading
from typing import Any
from uuid import uuid4

from pulse_harness.adapters.chat_http import ChatHTTPModelAdapter, model_factory_from_env
from pulse_harness.core import HarnessScope
from pulse_system.service.kernel_runtime import KernelHarnessFactory
from pulse_system.service.runtime import RuntimeService, RuntimeServiceConfig
from pulse_system.core.pulse.context import ContextPulseConfig as PulseSettings

from . import __version__
from .execution import PageExecution
from .artifacts import PageArtifacts
from .language import RESPONSE_LANGUAGES, ResponseLanguage, install_language_advisor

MAX_MESSAGES = 500
_ENV_PROVIDER = object()


class PageError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PageService:
    """One HTTP view of one native World, with serialized page metadata writes."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        example: bool = False,
        allow_workspace_actions: bool = False,
        pulse: PulseSettings | None = None,
        posture: bool | None = None,
        model_factory: Callable[[HarnessScope], ChatHTTPModelAdapter] | None | object = _ENV_PROVIDER,
    ):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        workspace = self.data_dir / "workspace"
        workspace.mkdir(exist_ok=True)
        self._lock = threading.RLock()
        self.allow_workspace_actions = bool(allow_workspace_actions and not example)
        # Explicit example data must remain usable with no credentials and
        # must never consume a user's model budget, even if the shell has keys.
        factory = None if example else (
            model_factory_from_env() if model_factory is _ENV_PROVIDER else model_factory
        )
        self.configured = factory is not None
        self.model = None
        if factory is not None:
            adapter = factory(HarnessScope("per-pulse", "config", "config", 0, "config"))
            try:
                self.model = adapter.config.model
            finally:
                adapter.close()
        config = RuntimeServiceConfig(
            db_path=self.data_dir / "world.db",
            workspace=workspace,
            metrics_path=self.data_dir / "metrics.jsonl",
            harness_kind="kernel",
            kernel_db_path=self.data_dir / "kernel.db",
            kernel_workspace_root=workspace,
            kernel_allow_mutations=self.allow_workspace_actions,
            base_spontaneous_rate=0.0,
            silence_threshold=0.15,
            default_max_wait=0.3,
            connectivity_interval_ticks=None,
            living_concern_reentry_budget_per_tick=0,
            context_pulse=pulse or PulseSettings(),
        )
        harness = KernelHarnessFactory(
            db_path=self.data_dir / "kernel.db", workspace=workspace,
            model_factory=factory, allow_unconfigured=True, allow_mutations=self.allow_workspace_actions,
            enable_posture=False if example else posture,
        )
        self.runtime = RuntimeService(config, harness_factory=harness)
        self.execution = PageExecution(self.runtime)
        self.artifacts = PageArtifacts(self.runtime, execution=self.execution)
        from .learning import PageLearning
        self.learning = PageLearning(self)
        self._state_key = f"per-pulse-pages-v1:{self.runtime.world_id}"
        self._state = self.runtime.storage.load_component_state(self._state_key)
        if self._state is None:
            self._state = {
                "version": 1, "createdAt": _now(), "example": example,
                "origins": {}, "references": [], "messageOrigins": {},
            }
            self._save()
        elif bool(self._state.get("example")) != example:
            self.runtime.close()
            raise PageError(
                "workspace_mode_mismatch",
                "Use a separate data directory for the example workspace.", 409,
            )
        self.example = example
        # Reuse the native TurnAdvisor seam, never a second model loop.
        # Bind before running a session so each new turn snapshots its language.
        install_language_advisor(self.runtime.kernel_service, self._response_language)
        if example and not self._state.get("exampleSeeded"):
            self._seed_example()

    def _response_language(self) -> ResponseLanguage:
        with self._lock:
            value = self._state.get("settings", {}).get("responseLanguage", "auto")
            return value if value in RESPONSE_LANGUAGES else "auto"

    def settings(self) -> dict[str, ResponseLanguage]:
        return {"responseLanguage": self._response_language()}

    def update_settings(self, response_language: ResponseLanguage) -> dict[str, ResponseLanguage]:
        if response_language not in RESPONSE_LANGUAGES:
            raise PageError("invalid_request", "Choose a supported response language.", 422)
        with self._lock:
            previous = self._state.get("settings")
            self._state["settings"] = {"responseLanguage": response_language}
            try:
                self._save()
            except BaseException:
                if previous is None:
                    self._state.pop("settings", None)
                else:
                    self._state["settings"] = previous
                raise
            return self.settings()

    @property
    def provider(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "model": self.model,
            "workspaceActions": self.allow_workspace_actions,
            "label": "AI connected" if self.configured else "AI disconnected",
            "detail": (
                "Your configured model handles messages in each page's own context."
                if self.configured else
                "Example workspace. These are authored sample notes; no model is running."
                if self.example else
                "Messages are saved and queued. Configure a model and restart to continue."
            ),
        }

    async def start(self) -> None:
        if self.configured:
            await self.runtime.start()

    async def close(self) -> None:
        await self.runtime.stop(timeout=5)
        await asyncio.to_thread(self.runtime.close)

    def _center(self, page_id: str) -> str | None:
        front = self._front(page_id)
        return front.center_id if front else None

    def pulse_status(self) -> dict:
        with self._lock:
            controller = self.runtime.context_pulse
            state = controller.snapshot()
            centers = {front["center_id"]: front["id"] for front in self.runtime.list_task_fronts()}

            def page_id_for(center_id: str | None) -> str | None:
                # Only the native null center means Home. A historical/hidden
                # Work center must not acquire a fabricated Home provenance.
                return "home" if center_id is None else centers.get(center_id)

            for connection in state["connections"]:
                connection["sourcePageId"] = page_id_for(connection["source_center_id"])
                connection["targetPageId"] = page_id_for(connection["target_center_id"])
            for context in state["contexts"]:
                context["pageId"] = page_id_for(context["center_id"])
            for held in state["heldContexts"]:
                held["pageId"] = page_id_for(held["center_id"])
            for retained in state.get("retainedAttention", []):
                # A model's retained local thought has its own original causal
                # source. Do not infer a navigable message from a turn/action
                # identifier, or remap an unavailable historical Work to Home.
                retained["pageId"] = page_id_for(retained["center_id"])
                retained["sourcePageId"] = None
                retained["sourceMessageId"] = None
                source_id = retained.get("sourceEventId")
                source = self.runtime.causal_ledger.get_event(source_id) if source_id else None
                if (source is not None and source.world_id == self.runtime.world_id
                        and source.center_id == retained["center_id"]):
                    source_page = page_id_for(source.center_id)
                    retained["sourcePageId"] = source_page
                    if source_page is not None:
                        try:
                            # The original source may be a non-displayable
                            # background root. Only message-owned evidence
                            # receives a deep link, even beyond the last 500.
                            self._check_message(source_page, source.id)
                        except PageError:
                            pass
                        else:
                            retained["sourceMessageId"] = source.id
            for activation in state["recentActivations"]:
                # Activation IDs are native causal events, not automatically
                # displayable messages. Verify both the local event and its
                # causal parent against the *full* page lineage before the UI
                # may deep-link them. No fallback to Home for missing Works.
                receiver_page = page_id_for(activation["centerId"])
                activation["pageId"] = receiver_page
                activation["messageId"] = None
                if receiver_page is not None:
                    try:
                        self._check_message(receiver_page, activation["eventId"])
                    except PageError:
                        pass
                    else:
                        activation["messageId"] = activation["eventId"]
                source_id = activation["sourceEventId"]
                source = self.runtime.causal_ledger.get_event(source_id) if source_id else None
                activation["sourceMessageId"] = None
                if source is not None and source.world_id == self.runtime.world_id:
                    source_page = page_id_for(source.center_id)
                    activation["sourceCenterId"] = source.center_id
                    activation["sourcePageId"] = source_page
                    if source_page is not None:
                        try:
                            self._check_message(source_page, source.id)
                        except PageError:
                            pass
                        else:
                            activation["sourceMessageId"] = source.id
                else:
                    activation["sourceCenterId"] = None
                    activation["sourcePageId"] = None
            state["running"] = self.runtime.running and state["enabled"] and not state["paused"]
            state["providerConfigured"] = self.configured
            return state

    def set_pulse_paused(self, paused: bool) -> dict:
        with self._lock:
            self.runtime._require_runtime_owner()
            controller = self.runtime.context_pulse
            if not controller.config.enabled:
                raise PageError("pulse_not_enabled", "Start Per Pulse with --pulse to control background activity.", 409)
            controller.set_paused(paused)
            return {"pulse": self.pulse_status()}

    def set_context_paused(self, page_id: str, paused: bool) -> dict:
        with self._lock:
            self.runtime._require_runtime_owner()
            controller = self.runtime.context_pulse
            if not controller.config.enabled:
                raise PageError("pulse_not_enabled", "Start Per Pulse with --pulse to control background activity.", 409)
            front = self._front(page_id)
            try:
                controller.set_context_paused(front.center_id if front else None, paused)
            except ValueError as exc:
                raise PageError("context_unavailable", str(exc), 409) from exc
            return {"pulse": self.pulse_status()}

    def connect_pages(self, source_page_id: str, target_page_id: str,
                      weight: float = 0.6, kind: str = "excitatory") -> dict:
        with self._lock:
            self.runtime._require_runtime_owner()
            source, target = self._center(source_page_id), self._center(target_page_id)
            try:
                edge = self.runtime.context_pulse.connect(source, target, weight, kind)
            except ValueError as exc:
                raise PageError("invalid_connection", str(exc)) from exc
            return {"connection": {**asdict(edge), "sourcePageId": source_page_id,
                                    "targetPageId": target_page_id}, "pulse": self.pulse_status()}

    def disconnect_pages(self, source_page_id: str, target_page_id: str) -> dict:
        with self._lock:
            self.runtime._require_runtime_owner()
            removed = self.runtime.context_pulse.disconnect(
                self._center(source_page_id), self._center(target_page_id),
            )
            return {"removed": removed, "pulse": self.pulse_status()}

    def _save(self) -> None:
        self.runtime._require_runtime_owner()
        self.runtime.storage.save_component_state(self._state_key, self._state)

    def _front(self, page_id: str) -> Any:
        if page_id == "home":
            return None
        front = self.runtime.world.get_task_front(page_id)
        if front is None:
            raise PageError("page_not_found", "This page does not exist.", 404)
        return front

    def _rows(self, page_id: str, *, limit: int = MAX_MESSAGES + 1,
              event_id: str | None = None) -> list[dict]:
        front = self._front(page_id)
        return self.runtime._causal_message_projection(
            center_id=front.center_id if front else None,
            engram_id=front.focal_engram_id if front else self.runtime.continuity_engram_id,
            unattributed=front is None,
            # Offline input is real queued input. Do not hide it until a
            # provider admits a Kernel turn, and do not fabricate a transcript.
            session_admitted=False, limit=limit, event_id=event_id,
        )

    def _check_message(self, page_id: str, message_id: str | None) -> None:
        front = self._front(page_id)
        if message_id is None:
            return
        # Validate against the full durable scope, not just the visible window.
        storage = self.runtime.storage
        subject_id = front.focal_engram_id if front else self.runtime.continuity_engram_id
        with storage._lock:
            row = storage._conn.execute(
                "SELECT world_id, center_id, content FROM causal_events WHERE id = ? "
                "AND kind IN ('stimulus', 'spontaneous', 'propagation', 'assistant_result', 'delegation_result') "
                "AND engram_id IN (WITH RECURSIVE lineage(id) AS (SELECT ? UNION "
                "SELECT g.predecessor_id FROM generation_transitions g JOIN lineage l "
                "ON g.successor_id = l.id WHERE g.state = 'committed') SELECT id FROM lineage)",
                (message_id, subject_id),
            ).fetchone()
        center_id = front.center_id if front else None
        if row is None or row[0] != self.runtime.world_id or row[1] != center_id or row[2] is None:
            raise PageError("message_not_found", "The selected message does not belong to this page.", 404)

    @staticmethod
    def _status(rows: list[dict]) -> str:
        statuses = {row["status"] for row in rows}
        if statuses.intersection({"running", "admitted", "processing"}):
            return "working"
        if "queued" in statuses:
            return "queued"
        if rows and rows[-1]["status"] in {"failed", "uncertain"}:
            return "error"
        if rows and rows[-1]["status"] in {"blocked", "awaiting_approval"}:
            return "needs_attention"
        return "idle"

    def _summary(self, page_id: str, rows: list[dict] | None = None) -> dict:
        front = self._front(page_id)
        rows = self._rows(page_id) if rows is None else rows
        origin = self._state["origins"].get(page_id, {})
        created = front.created_at.isoformat() if front else self._state["createdAt"]
        updated = front.updated_at.isoformat() if front else created
        if rows:
            updated = max(updated, rows[-1]["timestamp"])
        execution = self.execution.snapshot(page_id)
        status = self._status(rows)
        if execution["pendingActions"]:
            status = "needs_attention"
        elif execution["status"] == "working":
            status = "working"
        elif (execution["recoveryRequired"] or execution["status"] == "needs_attention") and status != "error":
            status = "needs_attention"
        return {
            "id": page_id, "kind": "work" if front else "home",
            "title": front.title if front else "Home",
            "status": status, "createdAt": created, "updatedAt": updated,
            "sourcePageId": origin.get("pageId"),
            "sourceMessageId": origin.get("messageId"),
            "description": origin.get("description", "Your ongoing conversation." if front is None else ""),
        }

    def bootstrap(self) -> dict:
        with self._lock:
            pages = [self._summary("home")]
            pages.extend(self._summary(front["id"]) for front in self.runtime.list_task_fronts())
            return {
                "name": "Per Pulse", "version": __version__, "example": self.example,
                "workspaceId": self.runtime.world_id,
                "homeId": "home", "provider": self.provider, "pages": pages,
                "settings": self.settings(),
                "postureEnabled": self.learning.enabled,
            }

    def view(self, page_id: str, *, source_message_id: str | None = None) -> dict:
        with self._lock:
            self._check_message(page_id, source_message_id)
            artifacts = self.artifacts.snapshot(page_id)
            rows = self._rows(page_id)
            has_more = len(rows) > MAX_MESSAGES
            rows = rows[-MAX_MESSAGES:]
            summary_rows = rows
            earlier_source_id = None
            if source_message_id and not any(row["event_id"] == source_message_id for row in rows):
                # A source link into old history must survive the bounded recent
                # window, without expanding or copying the whole transcript.
                source_rows = self._rows(page_id, limit=1, event_id=source_message_id)
                if not source_rows:
                    raise PageError("message_not_found", "The selected message does not belong to this page.", 404)
                rows = source_rows + rows
                earlier_source_id = source_message_id
            refs = [dict(r) for r in self._state["references"] if r["sourcePageId"] == page_id]
            messages = []
            center_pages = {front["center_id"]: front["id"] for front in self.runtime.list_task_fronts()}
            for row in rows:
                origin = self._state["messageOrigins"].get(row["event_id"], {})
                activation = None
                if row["kind"] == "propagation" and row["metadata"].get("context_connection_id"):
                    source_center = row["metadata"].get("source_center_id")
                    origin = {"pageId": "home" if source_center is None else center_pages.get(source_center),
                              "messageId": row["parent_event_id"]}
                    activation = "propagation"
                messages.append({
                    "id": row["event_id"], "role": "system" if activation else row["role"], "content": row["content"],
                    "createdAt": row["timestamp"], "status": row["status"],
                    "sourcePageId": origin.get("pageId"),
                    "sourceMessageId": origin.get("messageId"),
                    "activation": activation,
                    "references": [r for r in refs if r["sourceMessageId"] == row["event_id"]],
                })
            crumbs, seen, cursor = [], set(), page_id
            while cursor and cursor not in seen:
                seen.add(cursor)
                summary = self._summary(cursor, summary_rows if cursor == page_id else None)
                crumbs.append({"id": cursor, "title": summary["title"]})
                cursor = summary["sourcePageId"]
            if page_id != "home" and "home" not in seen:
                crumbs.append({"id": "home", "title": "Home"})
            return {
                "page": self._summary(page_id, summary_rows), "messages": messages,
                "references": refs,
                "backlinks": [dict(r) for r in self._state["references"] if r["targetPageId"] == page_id],
                "breadcrumbs": list(reversed(crumbs)), "provider": self.provider,
                "execution": self.execution.snapshot(page_id),
                "artifacts": artifacts["artifacts"], "artifactsTruncated": artifacts["truncated"],
                "hasMore": has_more,
                "earlierSourceMessageId": earlier_source_id,
            }

    def _reference(self, source: str, target: str, message_id: str | None,
                   label: str | None, kind: str = "reference") -> dict:
        self._check_message(source, message_id)
        target_title = self._summary(target)["title"]
        label = label or target_title
        for ref in self._state["references"]:
            if (ref["sourcePageId"], ref["sourceMessageId"], ref["targetPageId"], ref["kind"], ref["label"]) == (
                source, message_id, target, kind, label
            ):
                return dict(ref)
        ref = {
            "id": "ref_" + uuid4().hex, "sourcePageId": source,
            "sourceMessageId": message_id, "targetPageId": target,
            "label": label, "kind": kind, "createdAt": _now(),
        }
        self._state["references"].append(ref)
        return dict(ref)

    def create_page(self, title: str, instruction: str, source_page_id: str = "home",
                    source_message_id: str | None = None) -> dict:
        with self._lock:
            self._check_message(source_page_id, source_message_id)
            created = self.runtime.create_task_front(
                instruction, title=title, subject_engram_id=self.runtime.continuity_engram_id,
            )
            page_id = created["task_front"]["id"]
            self._state["origins"][page_id] = {
                "pageId": source_page_id, "messageId": source_message_id,
                "description": instruction[:240],
            }
            ref = self._reference(source_page_id, page_id, source_message_id, title, "created")
            self._save()
            view = self.view(page_id)
            return {"page": view["page"], "reference": ref, "initialMessageId": created["event_id"], "view": view}

    def send(self, page_id: str, content: str, references: list[dict] | None = None) -> dict:
        with self._lock:
            self._front(page_id)
            for ref in references or []:
                self._front(ref["targetPageId"])
            message_id = (
                self.runtime.inject(self.runtime.continuity_engram_id, content, source="user")
                if page_id == "home" else self.runtime.send_task_front_message(page_id, content)
            )
            for ref in references or []:
                self._reference(page_id, ref["targetPageId"], message_id, ref.get("label"))
            self._save()
            return {"messageId": message_id, "view": self.view(page_id)}

    def add_reference(self, page_id: str, target_page_id: str,
                      source_message_id: str | None = None, label: str | None = None) -> dict:
        with self._lock:
            ref = self._reference(page_id, target_page_id, source_message_id, label)
            self._save()
            # Keep a selected old source in the mutation response; otherwise
            # linking an earlier message collapses the UI to the last 500 rows.
            return {"reference": ref, "view": self.view(page_id, source_message_id=source_message_id)}

    def return_home(self, page_id: str, content: str, message_id: str | None = None) -> dict:
        with self._lock:
            if page_id == "home":
                raise PageError("already_home", "Choose a session page to bring a result home.")
            self._check_message(page_id, message_id)
            # This is the user's selected contribution, not an invented
            # assistant report or an implicit copy of the Work conversation.
            # A causal parent inherits its center in the native ledger, so a
            # cross-page reference belongs in the World link metadata instead.
            # Keeping this a fresh Home input preserves its actual Home lane.
            event_id = self.runtime.inject(
                self.runtime.continuity_engram_id, content, source="user",
            )
            self._state["messageOrigins"][event_id] = {"pageId": page_id, "messageId": message_id}
            ref = self._reference("home", page_id, event_id, None, "return")
            self._save()
            return {"messageId": event_id, "reference": ref, "view": self.view("home")}

    def rename(self, page_id: str, title: str) -> dict:
        with self._lock:
            if page_id == "home":
                raise PageError("home_title_fixed", "Home keeps its name.")
            self._front(page_id)
            self.runtime.update_task_front(page_id, title=title)
            return self.view(page_id)

    def _seed_example(self) -> None:
        # Authored example notes are actual user inputs in a separately marked
        # World. No assistant result is created without an actual Kernel turn.
        first = self.send("home", "Let’s give the launch a little room to grow.")
        notes = self.create_page(
            "Launch notes", "A place to work through the launch: the story, the first audience, and what to ship.",
            source_message_id=first["messageId"],
        )
        reading = self.create_page(
            "Reading room", "Keep useful references here, with a short note about why each one matters.",
            source_message_id=first["messageId"],
        )
        self.create_page("Product ideas", "Small ideas for a calmer workspace: linked pages, clear context, and a way back.")
        self.send(
            "home",
            "There is a space for the launch. Work through the details in Launch notes and keep this conversation open.\n\n"
            "Keep references in Reading room. These pages stay connected, so you can pick up the thread whenever you’re ready.\n\n"
            "Where would you like to begin?",
            references=[
                {"targetPageId": notes["page"]["id"], "label": "Launch notes"},
                {"targetPageId": reading["page"]["id"], "label": "Reading room"},
            ],
        )
        self.send(notes["page"]["id"], "Start with a small release that makes one complete path feel effortless: open a page, explore an idea, and bring a useful result home.")
        self._state["exampleSeeded"] = True
        self._save()
