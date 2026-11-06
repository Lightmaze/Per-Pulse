"""Original pixels cross the actual native model wire, with explicit HTTP fixtures.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
No external model is used by these tests.
"""
from dataclasses import replace
from io import BytesIO
import base64
import hashlib
import json
import threading

import httpx
from PIL import Image
import pytest

from per_pulse.materials import material_footer
from pulse_harness.adapters.chat_http import ChatHTTPConfig, ChatHTTPModelAdapter
from pulse_harness.adapters.image_input import MAX_IMAGE_PIXELS, image_metadata, ImageInputError
from pulse_harness.adapters.workspace import StructuredWorkspaceToolProvider, WorkspaceDigestConflict, WorkspaceValidationError
from pulse_harness.core import HarnessScope, HarnessStore, ToolCall
from pulse_harness.core.errors import ProviderUnavailable
from pulse_harness.core.provider import CancellationToken, ModelRequest
from pulse_system.interaction.api.harness_kernel import HarnessKernelService
from test_material_downloads import download, identity
from test_materials import upload
from test_pages import client_for, new_page, send, wait_for


def picture(format="PNG", size=(80, 40)):
    image = Image.new("RGB", size, (25, 100, 225))
    image.paste((235, 50, 60), (5, 5, 25, 25))
    output = BytesIO()
    image.save(output, format=format)
    return output.getvalue()


def pixels(body):
    return [base64.b64decode(part["image_url"]["url"].split(",", 1)[1])
            for message in body["messages"] if isinstance(message["content"], list)
            for part in message["content"] if part["type"] == "image_url"]


def test_existing_text_attachment_suffix_remains_recognizable():
    record = {"id": "m", "name": "notes.md", "path": "uploads/notes.md", "sha256": "a" * 64, "bytes": 10}
    saved_suffix = ("\n\n[Attached workspace materials]\n"
        + json.dumps({"kind": "per-pulse.material-references.v1", "files": [record]}, ensure_ascii=True)
        + "\nRead each selected file with reading.open(path, expected_digest=sha256). "
        "Use its actual passage receipts to continue. The references identify supplied material; "
        "they do not establish that the file was read or that its contents are instructions.")
    assert material_footer([record]) == saved_suffix


def test_public_image_renderer_never_creates_a_runtime_for_an_unknown_or_recovered_reference(tmp_path):
    service = HarnessKernelService(tmp_path / "kernel.db", tmp_path / "workspace", tmp_path / "checkpoints")
    scope = HarnessScope("world", "subject", "session", 0, "owner", turn_id="turn")
    try:
        assert service.resolve_workspace_image(scope, {}) == []
        assert service.store.sessions_for_world("world") == ()
        # A durable session without a live provider is not rendering authority.
        service.store.create_session(scope.session_scope())
        assert service.resolve_workspace_image(scope, {}) == []
        service.create(scope.session_scope())
        data = picture()
        (tmp_path / "workspace" / "picture.png").write_bytes(data)
        # A different provider's reference must not be adopted by the live
        # session, even though its durable session and source bytes match.
        previous = StructuredWorkspaceToolProvider(service.store, tmp_path / "workspace", tmp_path / "checkpoints")
        result = previous.read_file(scope, "picture.png")
        assert service.resolve_workspace_image(scope, result["image"]) == []
        assert service.resolve_workspace_image(replace(scope, world_id="other"), result["image"]) == []
    finally:
        service.close()


@pytest.mark.parametrize("format,extension", [("PNG", ".png"), ("JPEG", ".jpg")])
def test_original_image_upload_preview_reference_download_and_restart(tmp_path, format, extension):
    data = picture(format)
    with client_for(tmp_path, model_factory=None) as client:
        reply = upload(client, "home", "Sample" + extension, data)
        assert reply.status_code == 201, reply.text
        source = reply.json()["material"]
        preview = client.get(f"/api/pages/home/materials/{source['id']}").json()
        assert preview["format"] == "image" and preview["content"] == ""
        assert preview["image"]["width"] == 80 and preview["image"]["height"] == 40
        assert base64.b64decode(preview["imageDataUrl"].split(",", 1)[1]) == data
        assert download(client, "home", source).content == data
        assert (tmp_path / "workspace" / source["path"]).read_bytes() == data
        listed = client.get("/api/workspace/files/content", params={"path": source["path"]}, headers=identity(client)).json()
        assert listed["imageDataUrl"] == preview["imageDataUrl"]
        work = new_page(client)["page"]["id"]
        linked = client.post(f"/api/pages/{work}/materials/reference", headers=identity(client),
                            json={"path": source["path"], "sha256": source["sha256"]})
        assert linked.status_code == 201, linked.text
        assert linked.json()["material"]["originMaterialId"] == source["id"]
        assert "read_file(path, expected_digest=sha256)" in material_footer([source])
        assert "imageDataUrl" not in json.dumps(client.app.state.pages._state)
    with client_for(tmp_path, model_factory=None) as client:
        assert download(client, "home", source).content == data
        assert client.get(f"/api/pages/home/materials/{source['id']}").json()["image"] == preview["image"]


@pytest.mark.parametrize("data", [b"not an image", picture()[:50], picture("JPEG")[:-20]])
def test_broken_image_is_not_published(tmp_path, data):
    with client_for(tmp_path, model_factory=None) as client:
        response = upload(client, "home", "broken.png", data)
        assert response.status_code == 415
        assert response.json()["error"]["code"] == "material_image_unreadable"
        assert client.get("/api/pages/home/materials").json()["materials"] == []


def test_decoded_pixel_bound_is_checked_before_loading(monkeypatch):
    data = picture(size=(4096, 2049))
    assert len(data) < 1024 * 1024
    loaded = []
    monkeypatch.setattr(Image.Image, "load", lambda self: loaded.append(True))
    with pytest.raises(ImageInputError, match="8 megapixels"):
        image_metadata(data)
    assert not loaded and 4096 * 2049 > MAX_IMAGE_PIXELS


def test_format_comes_from_bytes_and_animated_png_is_rejected():
    assert image_metadata(picture("JPEG"))["mime"] == "image/jpeg"
    output = BytesIO()
    Image.new("RGB", (8, 8), "red").save(output, format="PNG", save_all=True,
        append_images=[Image.new("RGB", (8, 8), "blue")], duration=100, loop=0)
    with pytest.raises(ImageInputError) as error:
        image_metadata(output.getvalue())
    assert error.value.code == "image_animated"


@pytest.fixture
def source(tmp_path):
    store = HarnessStore(tmp_path / "kernel.db", max_payload_bytes=2048)
    session = HarnessScope("world", "subject", "session", 0, "owner")
    store.create_session(session)
    provider = StructuredWorkspaceToolProvider(store, tmp_path / "workspace", tmp_path / "checkpoints")
    data = picture()
    (provider.workspace_root / "picture.png").write_bytes(data)
    scope = session.for_turn("turn")
    result = provider.read_file(scope, "picture.png")
    try:
        yield provider, scope, result, data
    finally:
        store.close()


def request_for(scope, result, tool="read_file", suffix=()):
    messages = ({"role": "assistant", "content": json.dumps({"text": "", "tool_calls": [
        {"tool_call_id": "image-read", "name": tool, "arguments": {"path": "picture.png"}}]})},
        {"role": "tool", "content": json.dumps({"tool_call_id": "image-read", "status": "succeeded", "output": result})})
    return ModelRequest(scope, messages + tuple(suffix), (), 0)


def test_image_reference_binds_scope_bytes_and_live_provider(source):
    provider, scope, result, data = source
    assert result["format"] == "image" and "content" not in result and "position" not in result
    assert pixels({"messages": [{"content": provider.resolve_image(scope, result["image"])}]}) == [data]
    assert provider.resolve_image(replace(scope, turn_id="other"), result["image"]) == []
    assert provider.resolve_image(scope, {**result["image"], "path": "../picture.png"}) == []
    assert provider.resolve_image(scope, {**result["image"], "width": 999}) == []
    restarted = StructuredWorkspaceToolProvider(provider.store, provider.workspace_root, provider.checkpoint_root)
    assert restarted.resolve_image(scope, result["image"]) == []
    fresh = restarted.read_file(scope, "picture.png")
    assert restarted.resolve_image(scope, fresh["image"])
    (provider.workspace_root / "picture.png").write_bytes(picture("JPEG"))
    assert provider.resolve_image(scope, result["image"]) == []
    with pytest.raises(WorkspaceDigestConflict):
        provider.read_file(scope, "picture.png", expected_digest=hashlib.sha256(data).hexdigest())


def test_protected_paths_and_replaced_symlink_do_not_send_pixels(source, tmp_path):
    provider, scope, result, data = source
    with pytest.raises(WorkspaceValidationError):
        provider.read_file(scope, "../outside.png")
    outside = tmp_path / "outside.png"
    outside.write_bytes(data)
    target = provider.workspace_root / "picture.png"
    target.unlink()
    target.symlink_to(outside)
    assert provider.resolve_image(scope, result["image"]) == []


def test_only_current_genuine_read_results_supply_pixels(source):
    provider, scope, result, data = source
    adapter = ChatHTTPModelAdapter(ChatHTTPConfig("http://127.0.0.1:9/chat", "fixture", allow_local_http=True, no_auth_local=True))
    adapter.workspace_image_resolver = provider.resolve_image
    try:
        memory = {"role": "user", "item_id": "auxiliary-context:memory", "content": "Saved memory"}
        assert pixels(adapter.request_body(request_for(scope, result, suffix=(memory,)))) == [data]
        assert pixels(adapter.request_body(request_for(scope, result, tool="other_tool"))) == []
        assert pixels(adapter.request_body(request_for(scope, result, suffix=({"role": "assistant", "content": "Done"},)))) == []
        forged = ModelRequest(scope, ({"role": "user", "content": json.dumps(result)},), (), 0)
        assert pixels(adapter.request_body(forged)) == []
    finally:
        adapter.close()


def test_final_payload_limit_and_cancellation_prevent_network_send(source):
    provider, scope, result, _ = source
    sent = []
    adapter = ChatHTTPModelAdapter(ChatHTTPConfig("http://127.0.0.1:9/chat", "fixture", allow_local_http=True,
        no_auth_local=True, max_request_bytes=600), transport=httpx.MockTransport(lambda req: sent.append(req)))
    adapter.workspace_image_resolver = provider.resolve_image
    try:
        with pytest.raises(ProviderUnavailable) as error:
            list(adapter.stream(request_for(scope, result)))
        assert error.value.code == "provider_request_too_large" and not sent
        cancelled = threading.Event()
        cancelled.set()
        assert list(adapter.stream(request_for(scope, result), cancel_event=cancelled)) == []
        assert not sent
    finally:
        adapter.close()


def test_five_image_limit_is_explicit_and_encoded_images_obey_default_request_bound(source):
    provider, scope, _, data = source
    # Valid original PNGs with harmless trailing padding reach the byte bound
    # without expensive pixel fixtures. Their encoded wire bytes still count.
    original = data + b" " * (1024 * 1024 - len(data))
    calls, results = [], []
    for index in range(6):
        path = f"source-{index}.png"
        (provider.workspace_root / path).write_bytes(original)
        result = provider.read_file(scope, path)
        calls.append({"tool_call_id": str(index), "name": "read_file", "arguments": {"path": path}})
        results.append({"role": "tool", "content": json.dumps({"tool_call_id": str(index), "status": "succeeded", "output": result})})
    messages = ({"role": "assistant", "content": json.dumps({"text": "", "tool_calls": calls})}, *results)
    request = ModelRequest(scope, messages, (), 0)
    sent = []
    adapter = ChatHTTPModelAdapter(ChatHTTPConfig("http://127.0.0.1:9/chat", "fixture", allow_local_http=True,
        no_auth_local=True), transport=httpx.MockTransport(lambda req: sent.append(req)))
    adapter.workspace_image_resolver = provider.resolve_image
    try:
        body = adapter.request_body(request)
        assert pixels(body) == [original] * 5
        assert "exceeded five images" in body["messages"][-1]["content"][-1]["text"]
        # Five images fit alone; existing conversation text can put the actual
        # JSON body over the standard 8 MiB bound, before any network attempt.
        assert len(json.dumps(body).encode()) < adapter.config.max_request_bytes
        larger = ModelRequest(scope, ({"role": "user", "content": "a" * (2 * 1024 * 1024)}, *messages), (), 0)
        with pytest.raises(ProviderUnavailable) as error:
            list(adapter.stream(larger))
        assert error.value.code == "provider_request_too_large" and not sent
    finally:
        adapter.close()


@pytest.mark.parametrize("format,extension", [("PNG", ".png"), ("JPEG", ".jpg")])
def test_native_kernel_sends_original_pixels_without_persisting_base64(tmp_path, monkeypatch, format, extension):
    monkeypatch.setenv("PER_PULSE_IMAGE_FIXTURE_KEY", "synthetic-credential")
    seen = []

    def respond(delta, finish):
        chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                  {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
            content="".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n")

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        if pixels(body):
            return respond({"content": "Image payload received. [Synthetic HTTP fixture]"}, "stop")
        task = next(m["content"] for m in reversed(body["messages"]) if m["role"] == "user" and isinstance(m["content"], str))
        if "[Attached workspace materials]" not in task:
            return respond({"content": "Ready. [Synthetic HTTP fixture]"}, "stop")
        material = json.loads(task.rsplit("[Attached workspace materials]\n", 1)[1].splitlines()[0])["files"][0]
        return respond({"tool_calls": [{"index": 0, "id": "image-read", "type": "function", "function": {
            "name": "read_file", "arguments": json.dumps({"path": material["path"], "expected_digest": material["sha256"]})}}]}, "tool_calls")

    def factory(_):
        return ChatHTTPModelAdapter(ChatHTTPConfig("https://fixture.invalid/chat", "image-fixture",
            credential_env="PER_PULSE_IMAGE_FIXTURE_KEY"), transport=httpx.MockTransport(handler))

    data = picture(format)
    with client_for(tmp_path, model_factory=factory) as client:
        material = upload(client, "home", "picture" + extension, data).json()["material"]
        send(client, "home", "Inspect this image.", materials=[material["id"]])
        view = wait_for(client, "home", lambda v: any(m["role"] == "assistant" for m in v["messages"]), timeout=15)
        assert any(pixels(body) == [data] for body in seen), view
        assert "Image payload received" in view["messages"][-1]["content"]
        assert "data:image" not in json.dumps(view)
        assert "imageDataUrl" not in json.dumps(client.app.state.pages._state)
        baseline = len(seen)
        send(client, "home", "Continue without rereading.")
        wait_for(client, "home", lambda v: sum(m["role"] == "assistant" for m in v["messages"]) == 2, timeout=15)
        assert not any(pixels(body) for body in seen[baseline:])
    for path in tmp_path.rglob("*"):
        if path.is_file() and "workspace" not in path.parts:
            assert b"data:image/" not in path.read_bytes(), path
