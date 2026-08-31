"""Deterministic loopback HTTP tests for the local Milestone 1A application."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import http.client
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import uuid

import pytest

from scene_agent.scene import sha256_file
from scene_agent.webapp import (
    MAX_JSON_BODY_BYTES,
    PLAYCANVAS_MODULE_ROUTE,
    PLAYCANVAS_VERSION,
    _HEAVY_OPERATION_LOCK,
    create_server,
    validate_bind_host,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MILESTONE_ROOT = REPOSITORY_ROOT / "outputs" / "milestone1"
DECODER_SCRIPT = REPOSITORY_ROOT / "scripts" / "decode_compressed_ply.mjs"


def _node_playcanvas_available() -> bool:
    node = shutil.which("node")
    if node is None or not DECODER_SCRIPT.is_file():
        return False
    probe = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            "import 'playcanvas/build/playcanvas/src/scene/gsplat/gsplat-compressed-data.js'",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.returncode == 0


@contextmanager
def _running_server(*, web_root: Path | None = None):
    server = create_server(
        "127.0.0.1",
        0,
        repository_root=REPOSITORY_ROOT,
        web_root=web_root,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def server_port():
    with _running_server() as port:
        yield port


def _request(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], dict[str, object]]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        raw = response.read()
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
        return response.status, {key.lower(): value for key, value in response.getheaders()}, parsed
    finally:
        connection.close()


def _request_bytes(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        return response.status, {key.lower(): value for key, value in response.getheaders()}, response.read()
    finally:
        connection.close()


def _post(port: int, path: str, payload: object) -> tuple[int, dict[str, str], dict[str, object]]:
    body = json.dumps(payload).encode("utf-8")
    return _request(
        port,
        "POST",
        path,
        body=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )


def _raw_http_request(port: int, request: bytes) -> tuple[bytes, dict[str, object]]:
    with socket.create_connection(("127.0.0.1", port), timeout=10) as client:
        client.sendall(request)
        client.shutdown(socket.SHUT_WR)
        response = bytearray()
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            response.extend(chunk)
    header, body = bytes(response).split(b"\r\n\r\n", 1)
    return header, json.loads(body)


def _fresh_output_name(kind: str) -> str:
    return f"webapp-{kind}-{uuid.uuid4().hex}.ply"


def _remove_owned_output(name: str) -> None:
    path = MILESTONE_ROOT / name
    if path.is_file() and not path.is_symlink():
        path.unlink()


def test_status_is_local_cpu_only_json_without_cors(server_port: int):
    status, headers, payload = _request(server_port, "GET", "/api/status")

    assert status == 200
    assert payload == {
        "ok": True,
        "service": "scene-agent-ui",
        "local_only": True,
        "compute": "cpu",
        "busy": False,
        "operations": ["inspect", "decode", "roundtrip"],
    }
    assert headers["content-type"].startswith("application/json")
    assert "access-control-allow-origin" not in headers
    assert headers["x-content-type-options"] == "nosniff"


def test_exact_pinned_playcanvas_module_is_locally_served_without_cors(server_port: int):
    expected = REPOSITORY_ROOT / "node_modules" / "playcanvas" / "build" / "playcanvas.mjs"
    status, headers, body = _request_bytes(server_port, "GET", PLAYCANVAS_MODULE_ROUTE)

    assert PLAYCANVAS_VERSION == "2.3.3"
    assert status == 200
    assert body == expected.read_bytes()
    assert hashlib.sha256(body).hexdigest() == "4b18241d684e3676109100f61aa3ad3488f8f95f632fdbb4433290a315dbc875"
    assert headers["content-type"] == "application/javascript; charset=utf-8"
    assert headers["content-length"] == str(expected.stat().st_size)
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["cross-origin-resource-policy"] == "same-origin"
    assert "access-control-allow-origin" not in headers


def test_page_csp_allows_only_the_local_playcanvas_sort_worker(server_port: int):
    status, headers, body = _request_bytes(server_port, "GET", "/")

    assert status == 200
    assert b'type="module" src="/static/viewer.js"' in body
    policy = headers["content-security-policy"]
    assert "default-src 'self'" in policy
    assert "connect-src 'self'" in policy
    assert "script-src 'self'" in policy
    assert "worker-src blob:" in policy
    assert "unsafe-inline" not in policy
    assert "unsafe-eval" not in policy


@pytest.mark.parametrize(
    "target",
    [
        "/vendor/playcanvas-2.3.3.js",
        "/vendor/playcanvas-2.3.3.mjs/extra",
        "/vendor/playcanvas-2.3.3.mjs?cache=1",
        "/vendor/%2e%2e/package.json",
        "/vendor/node_modules/playcanvas/build/playcanvas.mjs",
    ],
)
def test_vendor_route_rejects_near_misses_queries_and_traversal(server_port: int, target: str):
    status, headers, payload = _request(server_port, "GET", target)

    assert status == 404
    assert payload["ok"] is False
    assert "access-control-allow-origin" not in headers


@pytest.mark.parametrize("method", ["POST", "HEAD", "OPTIONS"])
def test_playcanvas_module_allows_get_only(server_port: int, method: str):
    body = b"{}" if method == "POST" else None
    headers = {"Content-Type": "application/json", "Content-Length": "2"} if body else None
    status, response_headers, payload = _request(
        server_port,
        method,
        PLAYCANVAS_MODULE_ROUTE,
        body=body,
        headers=headers,
    )

    assert status == 405
    assert response_headers["allow"] == "GET"
    if method == "HEAD":
        assert payload == {}
    else:
        assert payload["error"]["type"] == "method_not_allowed"


@pytest.mark.parametrize("version", ["2.3.2", "2.3.4", "latest"])
def test_server_fails_closed_for_mismatched_playcanvas_version(tmp_path: Path, version: str):
    (tmp_path / "SPEC.md").write_text("spec", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("agents", encoding="utf-8")
    package_root = tmp_path / "node_modules" / "playcanvas"
    package_root.mkdir(parents=True)
    (package_root / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")

    with pytest.raises(RuntimeError, match="PlayCanvas 2.3.3 is required"):
        create_server("127.0.0.1", 0, repository_root=tmp_path, viewer_sources={})


def test_server_fails_closed_when_pinned_playcanvas_module_is_missing(tmp_path: Path):
    (tmp_path / "SPEC.md").write_text("spec", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("agents", encoding="utf-8")
    package_root = tmp_path / "node_modules" / "playcanvas"
    package_root.mkdir(parents=True)
    (package_root / "package.json").write_text(
        json.dumps({"version": PLAYCANVAS_VERSION}),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="PlayCanvas 2.3.3 installation is unavailable"):
        create_server("127.0.0.1", 0, repository_root=tmp_path, viewer_sources={})


def test_server_fails_closed_when_pinned_playcanvas_module_digest_differs(tmp_path: Path):
    (tmp_path / "SPEC.md").write_text("spec", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("agents", encoding="utf-8")
    package_root = tmp_path / "node_modules" / "playcanvas"
    build_root = package_root / "build"
    build_root.mkdir(parents=True)
    (package_root / "package.json").write_text(
        json.dumps({"version": PLAYCANVAS_VERSION}),
        encoding="utf-8",
    )
    (build_root / "playcanvas.mjs").write_text("export const version = '2.3.3';", encoding="utf-8")

    with pytest.raises(RuntimeError, match="integrity validation"):
        create_server("127.0.0.1", 0, repository_root=tmp_path, viewer_sources={})


@pytest.mark.parametrize(
    "host",
    ["attacker.example", "127.0.0.1.attacker.example", "[::1", "::1", "localhost:bad"],
)
def test_http_host_header_rejects_non_loopback_and_ambiguous_values(
    server_port: int,
    host: str,
):
    status, _, payload = _request(
        server_port,
        "GET",
        "/api/status",
        headers={"Host": host},
    )

    assert status == 400
    assert payload == {
        "ok": False,
        "error": {
            "type": "invalid_host",
            "message": "Host must identify an explicit loopback address",
        },
    }


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:8765", "localhost", "[::1]", "[::1]:8765"])
def test_http_host_header_accepts_loopback_forms(server_port: int, host: str):
    status, _, payload = _request(
        server_port,
        "GET",
        "/api/status",
        headers={"Host": host},
    )

    assert status == 200
    assert payload["ok"] is True


def test_very_long_host_port_returns_controlled_json_without_traceback(
    server_port: int,
    capsys,
):
    long_port = b"9" * 5000
    header, payload = _raw_http_request(
        server_port,
        b"GET /api/status HTTP/1.1\r\n"
        b"Host: 127.0.0.1:" + long_port + b"\r\n"
        b"Connection: close\r\n\r\n",
    )

    assert b" 400 " in header.splitlines()[0]
    assert payload["error"]["type"] == "invalid_host"
    assert "Traceback" not in capsys.readouterr().err


def test_malformed_absolute_request_target_returns_json_without_traceback(
    server_port: int,
    capsys,
):
    request = (
        b"GET http://[::1 HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Connection: close\r\n\r\n"
    )
    header, payload = _raw_http_request(server_port, request)
    assert b" 400 " in header.splitlines()[0]
    assert payload == {
        "ok": False,
        "error": {
            "type": "invalid_request_target",
            "message": "request target is malformed",
        },
    }
    assert "Traceback" not in capsys.readouterr().err


def test_inspect_reports_allowlisted_fields_and_preserves_source(server_port: int, compressed_factory):
    source = compressed_factory(vertex_count=4)
    before = sha256_file(source)

    status, _, payload = _post(server_port, "/api/inspect", {"source_path": str(source)})

    assert status == 200
    assert payload["ok"] is True
    assert payload["format"] == "playcanvas_compressed_ply"
    assert payload["inspection"]["gaussian_count"] == 4
    assert payload["source"] == {"sha256": before, "size_bytes": source.stat().st_size}
    assert "source_path" not in json.dumps(payload)
    assert sha256_file(source) == before


def test_invalid_ply_error_redacts_path_and_parser_context(server_port: int, tmp_path: Path):
    source = tmp_path / "sensitive-scene-location.ply"
    source.write_bytes(b"not a valid PLY")

    status, _, payload = _post(server_port, "/api/inspect", {"source_path": str(source)})

    assert status == 422
    assert payload == {
        "ok": False,
        "error": {
            "type": "invalid_ply",
            "message": "source is not a valid supported PLY file",
        },
    }
    serialized = json.dumps(payload)
    assert str(source) not in serialized
    assert "sensitive-scene-location" not in serialized


def test_outputs_lists_relative_regular_artifacts_only(server_port: int, canonical_factory):
    source = canonical_factory(vertex_count=2)
    output_name = _fresh_output_name("listing")
    listed = MILESTONE_ROOT / output_name
    shutil.copyfile(source, listed)
    try:
        status, _, payload = _request(server_port, "GET", "/api/outputs")

        assert status == 200
        assert payload["ok"] is True
        assert payload["root"] == "outputs/milestone1"
        names = [item["name"] for item in payload["outputs"]]
        assert output_name in names
        assert all(not Path(name).is_absolute() and ".." not in Path(name).parts for name in names)
    finally:
        _remove_owned_output(output_name)


@pytest.mark.parametrize(
    ("body", "headers", "expected_status", "error_type"),
    [
        (b"{broken", {"Content-Type": "application/json", "Content-Length": "7"}, 400, "malformed_json"),
        (b"[]", {"Content-Type": "application/json", "Content-Length": "2"}, 400, "invalid_request"),
        (b"{}", {"Content-Type": "text/plain", "Content-Length": "2"}, 415, "unsupported_media_type"),
        (
            b'{"source_path":"a","source_path":"b"}',
            {"Content-Type": "application/json", "Content-Length": "39"},
            400,
            "malformed_json",
        ),
    ],
)
def test_post_rejects_malformed_or_non_json_bodies(
    server_port: int,
    body: bytes,
    headers: dict[str, str],
    expected_status: int,
    error_type: str,
):
    # Derive the length here so the duplicate-key case cannot accidentally test truncation.
    headers = {**headers, "Content-Length": str(len(body))}
    status, _, payload = _request(server_port, "POST", "/api/inspect", body=body, headers=headers)

    assert status == expected_status
    assert payload["ok"] is False
    assert payload["error"]["type"] == error_type


@pytest.mark.parametrize("constant", [b"NaN", b"Infinity", b"-Infinity"])
def test_post_rejects_nonstandard_json_constants(
    server_port: int,
    constant: bytes,
):
    body = b'{"source_path":' + constant + b"}"
    status, _, payload = _request(
        server_port,
        "POST",
        "/api/inspect",
        body=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )

    serialized = json.dumps(payload).encode("utf-8")
    assert status == 400
    assert payload == {
        "ok": False,
        "error": {
            "type": "malformed_json",
            "message": "request body is not valid JSON",
        },
    }
    assert constant not in serialized


def test_duplicate_json_key_error_redacts_sensitive_key_name(server_port: int):
    sensitive_key = b"private_patient_scene_path"
    body = b'{"' + sensitive_key + b'":"a","' + sensitive_key + b'":"b"}'
    status, _, payload = _request(
        server_port,
        "POST",
        "/api/inspect",
        body=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )

    serialized = json.dumps(payload).encode("utf-8")
    assert status == 400
    assert payload == {
        "ok": False,
        "error": {
            "type": "malformed_json",
            "message": "request body is not valid JSON",
        },
    }
    assert sensitive_key not in serialized


def test_post_enforces_json_body_limit(server_port: int):
    body = b" " * (MAX_JSON_BODY_BYTES + 1)
    status, _, payload = _request(
        server_port,
        "POST",
        "/api/inspect",
        body=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )

    assert status == 413
    assert payload == {
        "ok": False,
        "error": {
            "type": "body_too_large",
            "message": f"JSON body exceeds {MAX_JSON_BODY_BYTES} bytes",
        },
    }


@pytest.mark.parametrize(
    ("method", "path", "allow"),
    [
        ("GET", "/api/inspect", "POST"),
        ("POST", "/api/status", "GET"),
        ("PUT", "/api/decode", "GET, POST"),
        ("OPTIONS", "/api/status", "GET, POST"),
    ],
)
def test_method_errors_are_json(server_port: int, method: str, path: str, allow: str):
    body = b"{}" if method == "POST" else None
    headers = {"Content-Type": "application/json", "Content-Length": "2"} if body else None
    status, response_headers, payload = _request(server_port, method, path, body=body, headers=headers)

    assert status == 405
    assert response_headers["allow"] == allow
    assert payload["ok"] is False
    assert payload["error"]["type"] == "method_not_allowed"


def test_unknown_method_cannot_bypass_malicious_host_rejection(server_port: int):
    header, payload = _raw_http_request(
        server_port,
        b"BREW /api/status HTTP/1.1\r\n"
        b"Host: attacker.example\r\n"
        b"Connection: close\r\n\r\n",
    )

    assert b" 400 " in header.splitlines()[0]
    assert payload["error"]["type"] == "invalid_host"
    assert b"501" not in header.splitlines()[0]


def test_parser_syntax_error_never_reflects_request_line_or_path(server_port: int):
    sensitive_path = b"/tmp/private-scene-name.ply"
    header, payload = _raw_http_request(
        server_port,
        b"GET " + sensitive_path + b" extra HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
    )

    serialized = json.dumps(payload).encode("utf-8")
    assert b" 400 " in header.splitlines()[0]
    assert payload == {
        "ok": False,
        "error": {"type": "http_error", "message": "Bad Request"},
    }
    assert sensitive_path not in serialized
    assert b"GET" not in serialized


def test_duplicate_content_length_is_rejected_before_dispatch(server_port: int):
    header, payload = _raw_http_request(
        server_port,
        b"POST /api/inspect HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 2\r\n"
        b"Content-Length: 2\r\n"
        b"Connection: close\r\n\r\n{}",
    )

    assert b" 400 " in header.splitlines()[0]
    assert payload["error"]["type"] == "invalid_request"


@pytest.mark.parametrize(
    "transfer_headers",
    [
        b"Transfer-Encoding:\r\n",
        b"Transfer-Encoding:\r\nTransfer-Encoding: chunked\r\n",
    ],
)
def test_any_transfer_encoding_header_is_rejected(
    server_port: int,
    transfer_headers: bytes,
):
    header, payload = _raw_http_request(
        server_port,
        b"POST /api/inspect HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        + transfer_headers
        + b"Content-Length: 2\r\n"
        b"Connection: close\r\n\r\n{}",
    )

    assert b" 400 " in header.splitlines()[0]
    assert payload["error"]["type"] == "invalid_request"


def test_very_long_content_length_returns_controlled_json_without_traceback(
    server_port: int,
    capsys,
):
    long_length = b"9" * 5000
    header, payload = _raw_http_request(
        server_port,
        b"POST /api/inspect HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + long_length + b"\r\n"
        b"Connection: close\r\n\r\n",
    )

    assert b" 400 " in header.splitlines()[0]
    assert payload["error"]["type"] == "invalid_request"
    assert "Traceback" not in capsys.readouterr().err


def test_oversized_json_integer_returns_redacted_malformed_json(
    server_port: int,
    capsys,
):
    integer_token = b"9" * 5000
    body = b'{"source_path":' + integer_token + b"}"
    header, payload = _raw_http_request(
        server_port,
        b"POST /api/inspect HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
        b"Connection: close\r\n\r\n" + body,
    )

    serialized = json.dumps(payload).encode("utf-8")
    assert b" 400 " in header.splitlines()[0]
    assert payload == {
        "ok": False,
        "error": {
            "type": "malformed_json",
            "message": "request body is not valid JSON",
        },
    }
    assert integer_token[:100] not in serialized
    assert "Traceback" not in capsys.readouterr().err


@pytest.mark.parametrize("content_length", [b"+2", b"02", b"2.0"])
def test_noncanonical_content_length_is_rejected(
    server_port: int,
    content_length: bytes,
):
    header, payload = _raw_http_request(
        server_port,
        b"POST /api/inspect HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + content_length + b"\r\n"
        b"Connection: close\r\n\r\n{}",
    )

    assert b" 400 " in header.splitlines()[0]
    assert payload["error"]["type"] == "invalid_request"


def test_declared_length_short_read_returns_controlled_json(server_port: int):
    header, payload = _raw_http_request(
        server_port,
        b"POST /api/inspect HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 102\r\n"
        b"Connection: close\r\n\r\n{}",
    )

    assert b" 400 " in header.splitlines()[0]
    assert payload == {
        "ok": False,
        "error": {
            "type": "truncated_body",
            "message": "request body ended before Content-Length bytes were received",
        },
    }


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "example.com", "", "127.0.0.2"])
def test_launcher_rejects_non_loopback_bind_hosts(host: str):
    with pytest.raises(ValueError, match="loopback|remote"):
        validate_bind_host(host)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", " LOCALHOST "])
def test_launcher_accepts_only_explicit_loopback_bind_hosts(host: str):
    assert validate_bind_host(host) in {"127.0.0.1", "localhost", "::1"}


@pytest.mark.parametrize(
    "output_name",
    ["../escape.ply", "/tmp/escape.ply", "nested/../../escape.ply", "nested\\escape.ply", ".hidden.ply", "scene.txt"],
)
def test_decode_rejects_unsafe_output_names_before_running_decoder(
    server_port: int,
    compressed_factory,
    output_name: str,
):
    source = compressed_factory(vertex_count=2)
    status, _, payload = _post(
        server_port,
        "/api/decode",
        {"source_path": str(source), "output_name": output_name},
    )

    assert status == 400
    assert payload["ok"] is False
    assert payload["error"]["type"] == "invalid_output_name"


def test_endpoint_rejects_arbitrary_command_fields(server_port: int, compressed_factory):
    source = compressed_factory(vertex_count=2)
    status, _, payload = _post(
        server_port,
        "/api/decode",
        {"source_path": str(source), "command": "touch /tmp/not-allowed"},
    )

    assert status == 400
    assert payload["error"]["type"] == "invalid_request"
    assert "unsupported field" in payload["error"]["message"]


def test_roundtrip_requires_canonical_source_beneath_milestone_output(
    server_port: int,
    canonical_factory,
    tmp_path: Path,
):
    source = canonical_factory(vertex_count=3)
    outside = tmp_path / "outside-canonical.ply"
    shutil.copyfile(source, outside)

    status, _, payload = _post(server_port, "/api/roundtrip", {"canonical_path": str(outside)})

    assert status == 400
    assert payload["error"]["type"] == "unsafe_canonical_path"


def test_roundtrip_is_exact_and_preserves_source(server_port: int, canonical_factory):
    source = canonical_factory(vertex_count=5, unknown=("double", "quality"))
    before = sha256_file(source)
    output_name = _fresh_output_name("roundtrip")
    try:
        status, _, payload = _post(
            server_port,
            "/api/roundtrip",
            {"canonical_path": str(source), "output_name": output_name},
        )

        assert status == 200
        assert payload["ok"] is True
        assert payload["output"] == {"name": output_name}
        assert payload["comparison"]["equal"] is True
        assert payload["comparison"]["same_schema"] is True
        assert payload["comparison"]["same_values"] is True
        assert payload["source"]["sha256_before"] == before
        assert payload["source"]["sha256_after"] == before
        assert payload["runtime"]["elapsed_seconds"] >= 0
        assert payload["runtime"]["runtime_seconds"] == payload["runtime"]["elapsed_seconds"]
        assert payload["runtime"]["peak_memory_bytes"] > 0
        assert payload["runtime"]["peak_memory_mib"] > 0
        assert payload["runtime"]["memory_source"] == "process_lifetime_high_water"
        assert sha256_file(source) == before
        serialized = json.dumps(payload)
        assert "canonical_path" not in serialized
        assert str(source) not in serialized
    finally:
        _remove_owned_output(output_name)


def test_frontend_roundtrip_does_not_reuse_decode_output_name():
    javascript = (REPOSITORY_ROOT / "src" / "scene_agent" / "web" / "app.js").read_text(
        encoding="utf-8"
    )
    handler = javascript.split("async function handleRoundtrip", 1)[1].split(
        "async function handleRefreshOutputs", 1
    )[0]

    assert "const body = { canonical_path: path };" in handler
    assert "output_name" not in handler
    assert "optionalOutputName" not in handler


def test_busy_lock_returns_conflict_without_starting_heavy_operation(server_port: int, canonical_factory):
    source = canonical_factory(vertex_count=2)
    assert _HEAVY_OPERATION_LOCK.acquire(blocking=False)
    try:
        status, _, payload = _post(server_port, "/api/roundtrip", {"canonical_path": str(source)})
    finally:
        _HEAVY_OPERATION_LOCK.release()

    assert status == 409
    assert payload == {
        "ok": False,
        "error": {
            "type": "busy",
            "message": "another decode or round-trip operation is already running",
        },
    }


def test_decode_preserves_source_and_redacts_process_details(server_port: int, compressed_factory):
    if not _node_playcanvas_available():
        pytest.skip("Node.js or the pinned PlayCanvas decoder dependency is unavailable")
    source = compressed_factory(vertex_count=3)
    before = sha256_file(source)
    output_name = _fresh_output_name("decode")
    try:
        status, _, payload = _post(
            server_port,
            "/api/decode",
            {"source_path": str(source), "output_name": output_name},
        )

        assert status == 200
        assert payload["ok"] is True
        assert payload["gaussian_count"] == 3
        assert payload["source"] == {"sha256_before": before, "sha256_after": before}
        assert payload["output"] == {"name": output_name}
        assert sha256_file(source) == before
        serialized = json.dumps(payload)
        assert str(source) not in serialized
        assert "command" not in serialized
        assert "stdout" not in serialized
        assert "stderr" not in serialized
        assert "partial" not in serialized
    finally:
        _remove_owned_output(output_name)


def test_static_missing_and_traversal_return_safe_json_404(tmp_path: Path):
    web_root = tmp_path / "web"
    web_root.mkdir()
    with _running_server(web_root=web_root) as port:
        for path in ("/", "/static/missing.js", "/static/%2e%2e/secret.txt"):
            status, _, payload = _request(port, "GET", path)
            assert status == 404
            assert payload == {
                "ok": False,
                "error": {"type": "not_found", "message": "static asset was not found"},
            }


def test_package_module_help_works_without_starting_server():
    completed = subprocess.run(
        [sys.executable, "-m", "scene_agent", "--help"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "scene-agent-ui" in completed.stdout
    assert "--host" in completed.stdout
    assert "--port" in completed.stdout
