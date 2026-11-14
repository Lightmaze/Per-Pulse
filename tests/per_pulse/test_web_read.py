"""Network boundaries for public text reading; explicit synthetic HTTP fixtures.

Copyright 2026 Lightmaze Contributors. Licensed under Apache-2.0.
"""
import asyncio
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from pulse_harness.adapters import web_read as web
from pulse_harness.core.provider import CancellationRequested, CancellationToken


class Chunks(httpx.AsyncByteStream):
    def __init__(self, parts):
        self.parts = parts

    async def __aiter__(self):
        for part in self.parts:
            yield part


def read(url="https://page.example/start", token=None):
    return web.read({"url": url}, token or CancellationToken(), action_request_id="web-observation")


def network(monkeypatch, handler):
    calls = []
    monkeypatch.setattr(web, "_resolve", lambda host, token, deadline: ("8.8.8.8",))
    def receive(request):
        calls.append(request)
        return handler(request)
    monkeypatch.setattr(web, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(receive), trust_env=False))
    return calls


def html(body, **kwargs):
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8", **kwargs},
                          stream=Chunks([body.encode()]))


def test_text_links_original_host_sni_and_redirect_observation(monkeypatch):
    def respond(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/article", "set-cookie": "private=do-not-return"})
        return html('<title>A source</title><script>DO_NOT_READ</script><style>DO_NOT_READ</style>'
                    '<p>Actual café 🌍 text.</p><a href="/next">Next</a><img src="/picture">')
    calls = network(monkeypatch, respond)
    result = read()
    assert result["content"] == "A source\nActual café 🌍 text.\nNext"
    assert result["links"] == ["https://page.example/next"]
    assert result["source"]["requested_url"] == "https://page.example/start"
    assert result["source"]["url"] == "https://page.example/article"
    assert result["source"]["scope"] == "extracted_text"
    assert not result["source"]["truncated"]
    assert len(calls) == 2
    for request in calls:
        assert request.url.host == "8.8.8.8"
        assert request.headers["host"] == "page.example"
        assert request.extensions["sni_hostname"] == "page.example"
        assert not {"cookie", "authorization", "proxy-authorization"} & set(request.headers)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://example.com", "https://user:pass@example.com",
    "https://example.com:444/a", "https://example.com\\@127.0.0.1", "https://example.com/\nsecret"])
def test_unusable_url_never_dispatches(monkeypatch, url):
    calls = network(monkeypatch, lambda request: html("unexpected"))
    with pytest.raises(web.WebReadError, match="HTTPS"):
        read(url)
    assert not calls


@pytest.mark.parametrize("host", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1",
    "::ffff:10.0.0.1", "fc00::1", "0.0.0.0", "100.64.0.1", "224.0.0.1"])
def test_private_literal_never_opens_a_connection(host):
    with pytest.raises(web.WebReadError) as error:
        web._resolve(host, CancellationToken(), time.monotonic() + 5)
    assert error.value.code == "web_address_blocked"


def test_real_isolated_dns_process_checks_localhost_and_public_literal():
    with pytest.raises(web.WebReadError) as error:
        web._resolve("localhost", CancellationToken(), time.monotonic() + 5)
    assert error.value.code == "web_address_blocked"
    assert web._resolve("8.8.8.8", CancellationToken(), time.monotonic() + 5) == ("8.8.8.8",)


@pytest.mark.parametrize("cancel", [False, True])
def test_dns_timeout_or_cancellation_reaps_its_only_child(monkeypatch, cancel):
    started = []
    real = subprocess.Popen
    def slow(*args, **kwargs):
        process = real([sys.executable, "-I", "-c", "import time; time.sleep(30)"], **kwargs)
        started.append(process)
        return process
    monkeypatch.setattr(web.subprocess, "Popen", slow)
    monkeypatch.setattr(web, "DNS_SECONDS", .15)
    token = CancellationToken()
    timer = threading.Timer(.07, token.cancel) if cancel else None
    if timer:
        timer.start()
    try:
        before = time.monotonic()
        with pytest.raises(CancellationRequested if cancel else web.WebReadError):
            web._resolve("page.example", token, time.monotonic() + 3)
        assert time.monotonic() - before < 3
        assert len(started) == 1 and started[0].poll() is not None
        assert started[0].stdout.closed
    finally:
        if timer:
            timer.join()


def test_mixed_dns_answers_are_rejected_and_validated_address_is_pinned(monkeypatch):
    real = subprocess.Popen
    def result(*args, **kwargs):
        return real([sys.executable, "-I", "-c", "print('{\"addresses\":[\"8.8.8.8\",\"127.0.0.1\"],\"overflow\":false}')"], **kwargs)
    monkeypatch.setattr(web.subprocess, "Popen", result)
    with pytest.raises(web.WebReadError) as error:
        web._resolve("page.example", CancellationToken(), time.monotonic() + 5)
    assert error.value.code == "web_address_blocked"


def test_redirect_to_private_address_is_rechecked_before_connecting(monkeypatch):
    real_resolve = web._resolve
    calls = network(monkeypatch, lambda request: httpx.Response(302, headers={"location": "https://127.0.0.1/private"}))
    monkeypatch.setattr(web, "_resolve", lambda host, token, deadline: ("8.8.8.8",) if host == "page.example"
                        else real_resolve(host, token, deadline))
    with pytest.raises(web.WebReadError) as error:
        read()
    assert error.value.code == "web_address_blocked"
    assert len(calls) == 1


def test_redirect_loop_is_bounded(monkeypatch):
    calls = network(monkeypatch, lambda request: httpx.Response(302, headers={"location": "/again"}))
    with pytest.raises(web.WebReadError) as error:
        read()
    assert error.value.code == "web_redirect_failed"
    assert len(calls) == web.MAX_REDIRECTS + 1


@pytest.mark.parametrize("headers", [{"content-type": "application/pdf"},
    {"content-type": "image/png"}, {"content-type": "text/html", "content-encoding": "gzip"}])
def test_unsupported_or_compressed_body_is_not_read(monkeypatch, headers):
    def body():
        raise AssertionError("unacceptable content must not be consumed")
        yield b""
    network(monkeypatch, lambda request: httpx.Response(200, headers=headers, stream=Chunks(body())))
    with pytest.raises(web.WebReadError) as error:
        read()
    assert error.value.code == "web_content_unsupported"


def test_large_decoded_text_and_wire_bytes_are_explicitly_partial(monkeypatch):
    monkeypatch.setattr(web, "MAX_BYTES", 64)
    monkeypatch.setattr(web, "MAX_CHARS", 24)
    network(monkeypatch, lambda request: html("<p>" + "z" * 100 + "</p>"))
    result = read()
    assert result["content"] == "z" * 24
    assert result["source"]["observed_bytes"] == 64
    assert result["source"]["truncated"] is True


def test_long_unicode_text_can_be_read_completely_in_bound_ranges(monkeypatch):
    original = "Original café 山川 🌍.\n" * 3500 + "ONLY_AT_THE_REAL_END"
    calls = network(monkeypatch, lambda request: httpx.Response(200,
        headers={"content-type": "text/plain; charset=utf-8"}, stream=Chunks([original.encode()])))
    arguments = {"url": "https://page.example/long", "max_chars": 8191}
    pieces, observations = [], []
    while arguments is not None:
        result = web.read(arguments, CancellationToken(), action_request_id=f"range-{len(pieces)}")
        pieces.append(result["content"])
        observations.append(result)
        assert len(result["content"]) <= 8191
        assert result["source"]["text_end"] - result["source"]["text_start"] == len(result["content"])
        assert not result["source"]["network_truncated"]
        arguments = result["next"]
    assert "".join(pieces) == original
    assert "ONLY_AT_THE_REAL_END" not in pieces[0] and pieces[-1].endswith("ONLY_AT_THE_REAL_END")
    assert len({result["source"]["observation_sha256"] for result in observations}) == 1
    assert all(result["source"]["truncated"] for result in observations)
    assert observations[-1]["available_eof"] and len(calls) == len(pieces)
    end = web.read({"url": "https://page.example/long", "offset": len(original),
                    "expected_digest": observations[0]["source"]["observation_sha256"]},
                   CancellationToken(), action_request_id="eof")
    assert end["content"] == "" and end["available_eof"] and end["next"] is None


def test_continuation_does_not_mix_changed_text_or_resolved_source(monkeypatch):
    state = {"body": "First complete observed text " * 1200, "destination": "/one"}
    def respond(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": state["destination"]})
        return html(state["body"])
    network(monkeypatch, respond)
    first = read()
    assert first["next"] is not None
    state["body"] += "A late revision."
    with pytest.raises(web.WebReadError) as error:
        web.read(first["next"], CancellationToken(), action_request_id="changed")
    assert error.value.code == "web_observation_changed"
    state["body"] = state["body"].removesuffix("A late revision.")
    state["destination"] = "/two"
    with pytest.raises(web.WebReadError) as error:
        web.read(first["next"], CancellationToken(), action_request_id="redirected")
    assert error.value.code == "web_observation_changed"


def test_network_bound_tail_is_not_claimed_as_the_original_page_end(monkeypatch):
    monkeypatch.setattr(web, "MAX_BYTES", 100)
    monkeypatch.setattr(web, "MAX_CHARS", 30)
    calls = network(monkeypatch, lambda request: httpx.Response(200,
        headers={"content-type": "text/plain"}, stream=Chunks([b"x" * 100, b"UNOBSERVED_TAIL"])))
    arguments, pieces = {"url": "https://page.example/partial"}, []
    while arguments is not None:
        result = web.read(arguments, CancellationToken(), action_request_id="bounded")
        assert result["source"]["truncated"] and result["source"]["network_truncated"]
        pieces.append(result["content"])
        arguments = result["next"]
    assert "".join(pieces) == "x" * 100
    assert result["available_eof"] and "unknown" in result["reading_note"]
    assert len(calls) == 4


@pytest.mark.parametrize("arguments", [{"offset": 1}, {"offset": -1}, {"offset": True},
    {"max_chars": 0}, {"max_chars": web.MAX_CHARS + 1},
    {"max_chars": True}, {"expected_digest": None}, {"expected_digest": "wrong"}, {"extra": 1}])
def test_invalid_ranges_never_start_a_network_request(monkeypatch, arguments):
    calls = network(monkeypatch, lambda request: html("should not fetch"))
    with pytest.raises(web.WebReadError):
        web.read({"url": "https://page.example/long", **arguments}, CancellationToken(), action_request_id="invalid")
    assert calls == []


def test_body_cancellation_and_network_timeout_are_bounded(monkeypatch):
    token = CancellationToken()
    def body():
        yield b"First"
        token.cancel()
        yield b"Second"
    network(monkeypatch, lambda request: httpx.Response(200, headers={"content-type": "text/plain"}, stream=Chunks(body())))
    with pytest.raises(CancellationRequested):
        read(token=token)
    def timeout(request):
        raise httpx.ReadTimeout("synthetic timeout")
    network(monkeypatch, timeout)
    with pytest.raises(web.WebReadError) as error:
        read()
    assert error.value.code == "web_read_timeout"


@pytest.mark.parametrize("fallback", [False, True])
def test_real_tls_checks_original_hostname_when_connecting_to_fixed_ip(monkeypatch, fallback):
    """Actual loopback TLS socket; only the TCP destination is test-routed."""
    fixture = Path(__file__).parents[1] / "fixtures/web_tls"
    observed = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append(self.headers["Host"])
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Real TLS page")
        def log_message(self, *_):
            pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(fixture / "cert.pem", fixture / "key.pem")
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    unreachable = socket.socket()
    unreachable.bind(("127.0.0.1", 0))  # Bound but not listening: a refused connection.
    destinations = []
    def connect(current, address):
        destinations.append((address, 443))
        assert address in {"8.8.8.8", "1.1.1.1"}
        port = unreachable.getsockname()[1] if fallback and address == "8.8.8.8" else server.server_address[1]
        return current.copy_with(host="127.0.0.1", port=port)
    monkeypatch.setattr(web, "_connection_url", connect)
    if fallback:
        monkeypatch.setattr(web, "_DNS_SCRIPT", 'print(\'{"addresses":["8.8.8.8","1.1.1.1"],"overflow":false}\')')
    else:
        monkeypatch.setattr(web, "_resolve", lambda host, token, deadline: ("8.8.8.8",))
    trusted = ssl.create_default_context(cafile=str(fixture / "cert.pem"))
    monkeypatch.setattr(web, "_client", lambda: httpx.AsyncClient(verify=trusted, trust_env=False))
    try:
        assert read("https://page.example/page")["content"] == "Real TLS page"
        assert observed == ["page.example"]
        with pytest.raises(web.WebReadError) as error:
            read("https://different.example/page")
        assert error.value.code == "web_connection_failed"
        assert observed == ["page.example"], "wrong hostname must fail before HTTP"
        # HTTPX normalizes this as IDNA2008 xn--fa-hia.de. Python's legacy
        # Unicode DNS/TLS spelling fass.de must not substitute another origin.
        with pytest.raises(web.WebReadError) as error:
            read("https://faß.de/page")
        assert error.value.code == "web_connection_failed"
        assert observed == ["page.example"]
        assert len(destinations) == (6 if fallback else 3)
    finally:
        unreachable.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


@pytest.mark.parametrize("phase", ["headers", "body"])
@pytest.mark.parametrize("cancel", [False, True])
def test_real_tls_drip_cannot_extend_deadline_or_delay_stop(monkeypatch, phase, cancel):
    fixture = Path(__file__).parents[1] / "fixtures/web_tls"
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            try:
                prefix = (b"HTTP/1.1 200 OK\r\nX-Slow: " if phase == "headers" else
                          b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 10000\r\n\r\n")
                self.connection.sendall(prefix)
                for _ in range(100):
                    self.connection.sendall(b"x")
                    time.sleep(.03)
            except OSError:
                pass
        def log_message(self, *_):
            pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(fixture / "cert.pem", fixture / "key.pem")
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(web, "_resolve", lambda host, token, deadline: ("8.8.8.8",))
    monkeypatch.setattr(web, "_connection_url", lambda current, address: current.copy_with(
        host="127.0.0.1", port=server.server_address[1]))
    trusted = ssl.create_default_context(cafile=str(fixture / "cert.pem"))
    monkeypatch.setattr(web, "_client", lambda: httpx.AsyncClient(verify=trusted, trust_env=False))
    monkeypatch.setattr(web, "TOTAL_SECONDS", 5 if cancel else .2)
    token = CancellationToken()
    timer = threading.Timer(.12, token.cancel) if cancel else None
    try:
        if timer:
            timer.start()
        started = time.monotonic()
        if phase == "body" and not cancel:
            # A slow real TLS stream can produce a usable observed prefix,
            # while a machine that spent the deadline on TLS still times out.
            try:
                observed = read(token=token)
            except web.WebReadError as error:
                assert error.code == "web_read_timeout"
            else:
                assert observed["content"] and observed["source"]["network_interrupted"]
                assert observed["source"]["truncated"]
        else:
            with pytest.raises(CancellationRequested if cancel else web.WebReadError):
                read(token=token)
        assert time.monotonic() - started < 1, "new bytes must not restart the whole deadline"
    finally:
        if timer:
            timer.join()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.parametrize("failure", [httpx.ConnectError, httpx.ConnectTimeout])
def test_all_validated_addresses_can_fail_without_re_resolving(monkeypatch, failure):
    def unavailable(request):
        raise failure("synthetic connection failure")
    calls = network(monkeypatch, unavailable)
    resolved = []
    def addresses(host, token, deadline):
        resolved.append(host)
        return ("8.8.8.8", "1.1.1.1")
    monkeypatch.setattr(web, "_resolve", addresses)
    with pytest.raises(web.WebReadError) as error:
        read()
    assert error.value.code == ("web_read_timeout" if failure is httpx.ConnectTimeout else "web_connection_failed")
    assert resolved == ["page.example"]
    assert [request.url.host for request in calls] == ["8.8.8.8", "1.1.1.1"]
    assert all(request.headers["host"] == request.extensions["sni_hostname"] == "page.example" for request in calls)


def test_connection_timeout_can_fall_back_to_a_validated_address(monkeypatch):
    def respond(request):
        if request.url.host == "8.8.8.8":
            raise httpx.ConnectTimeout("synthetic unreachable first address")
        return html("Second address works.")
    calls = network(monkeypatch, respond)
    monkeypatch.setattr(web, "_resolve", lambda *args: ("8.8.8.8", "1.1.1.1"))
    assert read()["content"] == "Second address works."
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["http", "body_error", "body_timeout"])
def test_receiving_http_never_retries_another_address(monkeypatch, failure):
    def body():
        yield b"Partial observation"
        raise (httpx.ReadTimeout if failure == "body_timeout" else httpx.ReadError)("synthetic body failure")
    def respond(request):
        return httpx.Response(503 if failure == "http" else 200,
            headers={"content-type": "text/plain"}, stream=Chunks(body()))
    calls = network(monkeypatch, respond)
    monkeypatch.setattr(web, "_resolve", lambda *args: ("8.8.8.8", "1.1.1.1"))
    if failure == "http":
        with pytest.raises(web.WebReadError):
            read()
    else:
        observed = read()
        assert observed["content"] == "Partial observation"
        assert observed["source"]["truncated"] is True
        assert observed["source"]["network_interrupted"] is True
        assert observed["source"]["network_truncated"] is True
        assert observed["available_eof"] is True
        assert "only the observed prefix" in observed["reading_note"]
    assert len(calls) == 1


def test_interrupted_stream_with_no_text_remains_an_error(monkeypatch):
    def empty_body():
        yield b""  # A zero-byte chunk is not an observed text passage.
        raise httpx.ReadError("no text ever arrived")
    calls = network(monkeypatch, lambda request: httpx.Response(
        200, headers={"content-type": "text/plain"}, stream=Chunks(empty_body())))
    with pytest.raises(web.WebReadError) as error:
        read()
    assert error.value.code == "web_connection_failed"
    assert len(calls) == 1


def test_total_timeout_preserves_observed_prefix_without_refetch(monkeypatch):
    class StalledBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"Observed before the page stalled."
            await asyncio.sleep(1)
            yield b"Unobserved tail"
    calls = network(monkeypatch, lambda request: httpx.Response(
        200, headers={"content-type": "text/plain"}, stream=StalledBody()))
    monkeypatch.setattr(web, "TOTAL_SECONDS", .12)
    observed = read()
    assert observed["content"] == "Observed before the page stalled."
    assert observed["source"]["observed_bytes"] == len(b"Observed before the page stalled.")
    assert observed["source"]["truncated"] is True
    assert observed["source"]["network_interrupted"] is True
    assert observed["next"] is None
    assert "only the observed prefix" in observed["reading_note"]
    assert len(calls) == 1


@pytest.mark.parametrize("cancel", [False, True])
def test_total_deadline_or_stop_prevents_later_address_attempts(monkeypatch, cancel):
    calls = []
    async def connecting(request):
        calls.append(request)
        await asyncio.sleep(.3)
        raise httpx.ConnectTimeout("synthetic slow connection")
    monkeypatch.setattr(web, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(connecting), trust_env=False))
    monkeypatch.setattr(web, "_resolve", lambda *args: ("8.8.8.8", "1.1.1.1"))
    monkeypatch.setattr(web, "TOTAL_SECONDS", 5 if cancel else .06)
    token = CancellationToken()
    timer = threading.Timer(.03, token.cancel) if cancel else None
    try:
        if timer:
            timer.start()
        started = time.monotonic()
        with pytest.raises(CancellationRequested if cancel else web.WebReadError):
            read(token=token)
        assert time.monotonic() - started < .5
        assert len(calls) == 1
    finally:
        if timer:
            timer.join()


def test_stop_between_failed_connection_and_fallback_prevents_next_attempt(monkeypatch):
    token = CancellationToken()
    def failed(request):
        token.cancel()
        raise httpx.ConnectError("connection failed as Stop arrived")
    calls = network(monkeypatch, failed)
    monkeypatch.setattr(web, "_resolve", lambda *args: ("8.8.8.8", "1.1.1.1"))
    with pytest.raises(CancellationRequested):
        read(token=token)
    assert len(calls) == 1


def test_later_connection_attempts_share_the_original_total_deadline(monkeypatch):
    calls = []
    async def connecting(request):
        calls.append(request)
        await asyncio.sleep(.04 if len(calls) == 1 else .3)
        raise httpx.ConnectTimeout("synthetic first connection timeout")
    monkeypatch.setattr(web, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(connecting), trust_env=False))
    monkeypatch.setattr(web, "_resolve", lambda *args: ("8.8.8.8", "1.1.1.1", "9.9.9.9"))
    monkeypatch.setattr(web, "TOTAL_SECONDS", .1)
    started = time.monotonic()
    with pytest.raises(web.WebReadError) as error:
        read()
    assert error.value.code == "web_read_timeout"
    assert len(calls) == 2, "the next address must not receive a fresh total deadline"
    assert time.monotonic() - started < .3


@pytest.mark.parametrize("blocked", ["127.0.0.1", "::ffff:10.0.0.1"])
def test_one_private_answer_prevents_every_fallback_connection(monkeypatch, blocked):
    resolver = web._resolve
    calls = network(monkeypatch, lambda request: html("must not reach any address"))
    monkeypatch.setattr(web, "_resolve", resolver)
    answers = json.dumps({"addresses": ["8.8.8.8", blocked], "overflow": False})
    monkeypatch.setattr(web, "_DNS_SCRIPT", "print(" + repr(answers) + ")")
    with pytest.raises(web.WebReadError) as error:
        read()
    assert error.value.code == "web_address_blocked"
    assert not calls
