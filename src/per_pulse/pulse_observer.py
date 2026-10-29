"""Read-only wall-clock observation of one continuing Per Pulse Context Body.

Records bounded native status/provenance snapshots from an already-running
loopback workspace. It neither starts activations nor sends model requests.
This is observational evidence, not a provider quality or spending-cap test.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx


def _local_url(value: str) -> str:
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError as exc:
        raise ValueError("Invalid local service URL") from exc
    if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or url.username is not None or url.password is not None
            or url.path not in {"", "/"} or url.query or url.fragment
            or port is None or not 1 <= port <= 65535):
        raise ValueError("Observe only an explicit loopback HTTP URL with a port")
    return value.rstrip("/")


def _limits(duration: float, interval: float) -> None:
    if (type(duration) not in (int, float) or not math.isfinite(duration)
            or not 0 < duration <= 86400
            or type(interval) not in (int, float) or not math.isfinite(interval)
            or not 0 < interval <= 3600
            or duration / interval > 20000):
        raise ValueError("Duration must be at most 24 hours, with at most 20,000 observations")


def _read(client: httpx.Client, path: str) -> dict:
    response = client.get(path)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("Status endpoint did not return an object")
    return data


def observe(
    url: str, output: Path, *, duration_seconds: float, interval_seconds: float,
    client: httpx.Client | None = None, clock=time.monotonic, sleep=time.sleep,
) -> dict:
    """Observe an existing service without writing anything into its World.

    JSONL remains readable if interrupted; a final summary record distinguishes
    a completed observation from a partial one. Event counts are lower bounds
    because the server only exposes a recent activation window.
    """
    base = _local_url(url)
    _limits(duration_seconds, interval_seconds)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(output, flags, 0o600)
    owned_client = client is None
    if owned_client:
        client = httpx.Client(base_url=base, timeout=5.0, trust_env=False)
    start = clock()
    deadline = start + duration_seconds
    world_id = None
    service_instance_id = None
    seen: dict[str, dict] = {}
    samples = 0
    poll_errors = 0
    saturated = False
    connected_samples = 0
    running_samples = 0
    paused_samples = 0
    exit_reason = "completed"
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            def emit(record: dict) -> None:
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                stream.flush()

            emit({
                "type": "header", "schema": "per_pulse.pulse_observation.v1",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "duration_seconds": duration_seconds,
                "interval_seconds": interval_seconds,
                "method": "read_only_local_status",
                "caveat": "recent events are bounded; providerConfigured is not proof of real model quality or costs",
            })
            try:
                while True:
                    elapsed = clock() - start
                    if samples and elapsed > duration_seconds:
                        break
                    try:
                        # A single response binds the live service identity
                        # to the native status. Separate calls could straddle
                        # a restart and silently mix two different instances.
                        observation = _read(client, "/api/pulse/observation")
                        pulse = observation.get("pulse")
                        if not isinstance(pulse, dict):
                            raise ValueError("Observation has no native Pulse status")
                        current_id = observation.get("workspaceId")
                        if not isinstance(current_id, str) or not current_id:
                            raise ValueError("Bootstrap has no native World identity")
                        current_instance = observation.get("serviceInstanceId")
                        if not isinstance(current_instance, str) or not current_instance:
                            raise ValueError("Bootstrap has no service instance identity")
                        if world_id is None:
                            world_id = current_id
                            service_instance_id = current_instance
                        if current_id != world_id:
                            exit_reason = "world_changed"
                            emit({"type": "world_change", "elapsed_seconds": round(elapsed, 3)})
                            break
                        if current_instance != service_instance_id:
                            # A restarted process can retain the same World.
                            # It must not count as uninterrupted live execution.
                            exit_reason = "service_restarted"
                            emit({"type": "service_restart", "elapsed_seconds": round(elapsed, 3)})
                            break
                        recent = pulse.get("recentActivations")
                        if not isinstance(recent, list):
                            raise ValueError("Pulse status has no recent activation list")
                        # The native endpoint exposes only its recent window. A
                        # saturated window must never be called full coverage.
                        saturated |= len(recent) >= 20
                        changes = []
                        for item in recent:
                            if not isinstance(item, dict):
                                raise ValueError("Invalid native activation")
                            event_id = item.get("eventId")
                            status = item.get("status")
                            if not isinstance(event_id, str) or not event_id or not isinstance(status, str):
                                raise ValueError("Invalid native activation identity")
                            # The native service only exposes a receiving message
                            # after verifying its durable page lineage. Never
                            # promote a settled root without that exact link to
                            # evidence of a result the user can actually open.
                            message_id = item.get("messageId")
                            if message_id is not None and message_id != event_id:
                                raise ValueError("Invalid native receiving message identity")
                            slim = {
                                "eventId": event_id, "status": status,
                                "kind": item.get("kind"), "pageId": item.get("pageId"),
                                "messageId": message_id,
                                "sourceEventId": item.get("sourceEventId"),
                                "sourcePageId": item.get("sourcePageId"),
                            }
                            if seen.get(event_id) != slim:
                                seen[event_id] = slim
                                changes.append(slim)
                        connected = pulse.get("providerConfigured") is True
                        connected_samples += int(connected)
                        running_samples += int(pulse.get("running") is True)
                        paused_samples += int(pulse.get("paused") is True)
                        emit({
                            "type": "sample", "elapsed_seconds": round(elapsed, 3),
                            "utc": datetime.now(timezone.utc).isoformat(),
                            "enabled": pulse.get("enabled"), "paused": pulse.get("paused"),
                            "running": pulse.get("running"), "providerConfigured": connected,
                            "queue": pulse.get("queue"), "budget": pulse.get("budget"),
                            "providerTokenBudget": pulse.get("providerTokenBudget"),
                            "recent_window_size": len(recent),
                            "event_updates": changes,
                        })
                        samples += 1
                    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                        poll_errors += 1
                        # No response content, URL, credentials or transcripts
                        # are stored even if the local service is unhealthy.
                        emit({"type": "poll_error", "elapsed_seconds": round(elapsed, 3),
                              "error_type": type(exc).__name__})
                    remaining = deadline - clock()
                    if remaining <= 0:
                        break
                    sleep(min(interval_seconds, remaining))
            except KeyboardInterrupt:
                exit_reason = "interrupted"
            counts = Counter(
                event["pageId"] or "unknown"
                for event in seen.values()
                if event["status"] == "settled"
                and event["kind"] in {"spontaneous", "propagation"}
            )
            # A settled activation is not necessarily a navigable Work result.
            # Message identity comes from the server's source-checked page
            # projection, not from a guessed event-to-message relationship.
            work_results = Counter(
                event["pageId"] for event in seen.values()
                if event["status"] == "settled"
                and event["kind"] in {"spontaneous", "propagation"}
                and isinstance(event["pageId"], str)
                and event["pageId"] not in {"", "home"}
                and event["messageId"] == event["eventId"]
            )
            summary = {
                "type": "summary", "reason": exit_reason, "workspaceId": world_id,
                "serviceInstanceId": service_instance_id,
                "elapsed_seconds": round(clock() - start, 3),
                "successful_samples": samples, "poll_errors": poll_errors,
                "provider_configured_samples": connected_samples,
                "pulse_running_samples": running_samples,
                "pulse_paused_samples": paused_samples,
                "observed_events": len(seen),
                "observed_settled_background_by_page": dict(sorted(counts.items())),
                "observed_navigable_work_results_by_page": dict(sorted(work_results.items())),
                "observed_navigable_work_results": sum(work_results.values()),
                "recent_window_saturated": saturated,
                "full_event_coverage_claimed": False,
                "eligible_for_uninterrupted_observation":
                    exit_reason == "completed" and poll_errors == 0 and not saturated
                    and samples > 0,
                "provider_quality_or_cost_verified": False,
            }
            emit(summary)
            return summary
    finally:
        if owned_client:
            client.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Observe an already running local Per Pulse body without sending model requests."
    )
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--duration-seconds", type=float, default=3600)
    parser.add_argument("--interval-seconds", type=float, default=10)
    parser.add_argument("--output", type=Path, required=True,
                        help="New private JSONL evidence file (mode 0600; never overwritten).")
    args = parser.parse_args(argv)
    try:
        result = observe(args.url, args.output,
                         duration_seconds=args.duration_seconds,
                         interval_seconds=args.interval_seconds)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
