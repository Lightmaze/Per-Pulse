"""Native Work learning browser fixture; only HTTP model replies are synthetic.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import argparse
import json
import os
import threading

import httpx
import uvicorn

from per_pulse.app import create_app
from per_pulse.service import PulseSettings
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter


parser = argparse.ArgumentParser()
parser.add_argument("--data-dir", required=True)
parser.add_argument("--port", required=True, type=int)
args = parser.parse_args()
os.environ["PER_PULSE_WORK_LEARNING_FIXTURE"] = "synthetic-test-credential"
requests = []
lock = threading.Lock()


def handler(request):
    body = json.loads(request.content)
    with lock:
        requests.append(body["messages"])
    text = json.dumps(body["messages"])
    output = "Synthetic B synthesis." if "WORK_B_HISTORY" in text else "Synthetic A finding."
    chunks = [
        {"choices": [{"index": 0, "delta": {"content": output}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 32, "completion_tokens": 8, "total_tokens": 40}},
    ]
    stream = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
    return httpx.Response(200, headers={"content-type": "text/event-stream"},
                          content=stream + "data: [DONE]\n\n")


def factory(scope):
    return ChatHTTPModelAdapter(ChatHTTPConfig(
        "https://fixture.invalid/chat/completions", "synthetic-work-learning-fixture",
        credential_env="PER_PULSE_WORK_LEARNING_FIXTURE"), transport=httpx.MockTransport(handler))


app = create_app(args.data_dir, model_factory=factory, pulse=PulseSettings(
    enabled=True, spontaneous_rate=0, refractory_seconds=0,
    check_interval_seconds=0.05, propagation_threshold=0.01,
    max_propagation_depth=1, max_background_pulses_per_hour=4))


@app.get("/__work/requests")
def recorded():
    with lock:
        return {"requests": list(requests),
                "engramCount": len(app.state.pages.runtime.storage.list_engrams())}


route = app.router.routes.pop()
index = next(i for i, value in enumerate(app.router.routes)
             if getattr(value, "path", None) == "/{route:path}")
app.router.routes.insert(index, route)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
