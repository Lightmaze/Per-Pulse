"""Scripted decisions over actual uploaded CSV bytes and native tool results.

This fixture checks the integration, not a real model's arithmetic or learning.
Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
from __future__ import annotations

import csv
from decimal import Decimal
import io
import json

import httpx
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


class MaterialFixture:
    def __init__(self, setenv, *, before_read=None):
        setenv("PER_PULSE_MATERIAL_FIXTURE_KEY", "explicit-synthetic-credential")
        self.requests = []
        self.before_read = before_read
        self.reads = []

    @staticmethod
    def response(text=None, *, tool=None, arguments=None, sequence=0):
        delta = {"content": text} if tool is None else {"tool_calls": [{
            "index": 0, "id": f"fixture-{tool}-{sequence}", "type": "function",
            "function": {"name": tool, "arguments": json.dumps(arguments)},
        }]}
        chunks = [
            {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop" if tool is None else "tool_calls"}]},
        ]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            content="".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n")

    def handler(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        messages = body["messages"]
        if "WORK_MATERIAL_JSON" in json.dumps(messages):
            return self.response("PASS")
        current = max(i for i, message in enumerate(messages) if message["role"] == "user")
        task = str(messages[current]["content"])
        if "PROCESS_ATTACHED_CSV" not in task:
            return self.response("Ready to inspect source material. [Explicit synthetic fixture]")
        references = json.loads(task.rsplit("[Attached workspace materials]\n", 1)[1].splitlines()[0])
        source = references["files"][0]
        results = [json.loads(m["content"]) for m in messages[current + 1:] if m["role"] == "tool"]
        if not results:
            if self.before_read:
                self.before_read(source)
            return self.response(tool="reading", arguments={"op": "open", "path": source["path"],
                                                            "expected_digest": source["sha256"], "max_chars": 2000})
        if results[-1]["status"] != "succeeded":
            assert "WRONG_VERSION_PRIVATE" not in json.dumps(results[-1])
            return self.response("The supplied version changed before it could be read. [Explicit synthetic fixture]")
        passages = [result["output"] for result in results if result["output"].get("kind") == "pulse.reading-passage.v1"]
        last = passages[-1]
        assert all(passage["source"]["digest"] == source["sha256"] for passage in passages)
        if last["next"] is not None:
            return self.response(tool="reading", arguments={"op": "continue", "receipt_id": last["receipt_id"], "max_chars": 2000}, sequence=len(results))
        text = "".join(passage["content"] for passage in passages).removeprefix("\ufeff")
        rows = list(csv.DictReader(io.StringIO(text, newline="")))
        total = sum((Decimal(row["quantity"]) * Decimal(row["unit_price"]) for row in rows), Decimal(0))
        report = f"# Inventory report\n\nRows read: {len(rows)}\n\nTotal: {total:.2f}\n\nSource: {source['name']}\n\n[Explicit synthetic provider fixture; actual CSV and file effects.]\n"
        path = f"inventory-{source['id'][:8]}.md"
        if len(results) == len(passages):
            self.reads.append({"source": source, "content": text, "passages": len(passages), "report": report, "path": path})
            return self.response(tool="mutate_file", arguments={"path": path, "expected_digest": None, "content": report}, sequence=len(results))
        return self.response(f"Inventory total: {total:.2f}.\n\nThe report is saved in `{path}`.\n\n[Explicit synthetic provider fixture]")

    def factory(self, _scope):
        return ChatHTTPModelAdapter(ChatHTTPConfig(
            "https://fixture.invalid/chat/completions", "explicit-material-fixture",
            credential_env="PER_PULSE_MATERIAL_FIXTURE_KEY",
        ), transport=httpx.MockTransport(self.handler))
