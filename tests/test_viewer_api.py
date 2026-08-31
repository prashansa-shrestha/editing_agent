"""M1 contracts for the allowlisted viewer manifest and source stream."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import threading

import pytest

from scene_agent.scene import sha256_file
from scene_agent import viewer
from scene_agent.viewer import ViewerSourceChanged, open_viewer_stream
from scene_agent.webapp import create_server


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
KNOWN_SOURCE = Path(
    "/data/Desktop/Prashansa/major/scenebench/data/InteriorGS/"
    "0231_840445/3dgs_compressed.ply"
)
TEST_SCENE_ID = "fixture_scene"


def _source_config(path: Path) -> viewer.ViewerSourceConfig:
    return viewer.ViewerSourceConfig(
        path=path,
        coordinate_system=viewer.Z_UP_COORDINATE_SYSTEM,
    )


def _allowlist(path: Path) -> dict[str, viewer.ViewerSourceConfig]:
    return {TEST_SCENE_ID: _source_config(path)}


def _large_compressed_fixture(tmp_path: Path, vertex_count: int) -> Path:
    chunk_count = (vertex_count + 255) // 256
    lines = [
        "ply",
        "format binary_little_endian 1.0",
        f"element chunk {chunk_count}",
        *(f"property float {name}" for name in viewer.CHUNK_PROPERTIES),
        f"element vertex {vertex_count}",
        *(f"property uint {name}" for name in viewer.PACKED_VERTEX_PROPERTIES),
        f"element sh {vertex_count}",
        *(f"property uchar {name}" for name in viewer.SH_PROPERTIES),
        "end_header",
    ]
    header = ("\n".join(lines) + "\n").encode("ascii")
    chunk = struct.pack(
        "<18f",
        0,
        0,
        0,
        10,
        10,
        10,
        -1,
        -1,
        -1,
        0,
        0,
        0,
        0,
        0,
        0,
        1,
        1,
        1,
    )
    source = tmp_path / f"large-{vertex_count}.ply"
    packed_rotation = (512 << 20) | (512 << 10) | 512
    vertex = struct.pack("<4I", 0, packed_rotation, 0, 0)
    source.write_bytes(
        header
        + chunk * chunk_count
        + vertex * vertex_count
        + bytes(45 * vertex_count)
    )
    return source


@contextmanager
def _running_server(sources: dict[str, viewer.ViewerSourceConfig] | None):
    server = create_server(
        "127.0.0.1",
        0,
        repository_root=REPOSITORY_ROOT,
        viewer_sources=sources,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(
    port: int, method: str, target: str
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        connection.request(method, target)
        response = connection.getresponse()
        body = response.read()
        return (
            response.status,
            {name.lower(): value for name, value in response.getheaders()},
            body,
        )
    finally:
        connection.close()


def _json_request(
    port: int, method: str, target: str
) -> tuple[int, dict[str, str], dict[str, object]]:
    status, headers, body = _request(port, method, target)
    return status, headers, json.loads(body) if body else {}


def _payload_offset(path: Path) -> int:
    with path.open("rb") as handle:
        prefix = handle.read(16 * 1024)
    marker = b"end_header\n"
    return prefix.index(marker) + len(marker)


def _set_chunk_bounds(
    path: Path,
    bounds: list[tuple[float, float, float, float, float, float]],
) -> None:
    offset = _payload_offset(path)
    with path.open("r+b") as handle:
        for index, values in enumerate(bounds):
            handle.seek(offset + index * 18 * 4)
            handle.write(struct.pack("<6f", *values))


def _set_chunk_float(path: Path, property_index: int, value: float) -> None:
    with path.open("r+b") as handle:
        handle.seek(_payload_offset(path) + property_index * 4)
        handle.write(struct.pack("<f", value))


def _set_packed_rotation(path: Path, value: int) -> None:
    chunk_count = 1
    vertex_offset = _payload_offset(path) + chunk_count * 18 * 4
    with path.open("r+b") as handle:
        handle.seek(vertex_offset + 4)
        handle.write(struct.pack("<I", value))


def _playcanvas_aabb(path: Path) -> dict[str, list[float]]:
    script = """
import { readFileSync } from 'node:fs';
import { BoundingBox } from 'playcanvas/build/playcanvas/src/core/shape/bounding-box.js';
import { parseCompressedPly } from './scripts/decode_compressed_ply.mjs';
const data = parseCompressedPly(readFileSync(process.argv[1]));
const bounds = new BoundingBox();
data.calcAabb(bounds);
const minimum = bounds.getMin();
const maximum = bounds.getMax();
console.log(JSON.stringify({
    min: [minimum.x, minimum.y, minimum.z],
    max: [maximum.x, maximum.y, maximum.z]
}));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(path)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(completed.stdout)


def test_manifest_reports_only_public_geometry_and_preserves_source(tmp_path: Path):
    source = _large_compressed_fixture(tmp_path, 300)
    _set_chunk_bounds(
        source,
        [
            (-4.0, 2.0, -1.5, 6.0, 8.0, 3.0),
            (-9.0, -3.0, 0.5, 2.0, 12.0, 7.5),
        ],
    )
    before = (sha256_file(source), source.stat().st_size)
    renderer_aabb = _playcanvas_aabb(source)

    with _running_server(_allowlist(source)) as port:
        status, headers, payload = _json_request(
            port, "GET", f"/api/viewer/manifest?scene_id={TEST_SCENE_ID}"
        )

    assert status == 200
    assert payload == {
        "ok": True,
        "scene_id": TEST_SCENE_ID,
        "format": "playcanvas_compressed_ply",
        "sha256": before[0],
        "size_bytes": before[1],
        "gaussian_count": 300,
        "chunk_count": 2,
        "coordinate_system": {
            "world_up": "+Z",
            "floor_axes": ["+X", "+Y"],
            "units": "scene_units",
        },
        "scene_aabb": {
            "min": renderer_aabb["min"],
            "max": renderer_aabb["max"],
        },
    }
    assert "access-control-allow-origin" not in headers
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert str(source) not in json.dumps(payload)
    assert (sha256_file(source), source.stat().st_size) == before


def test_source_stream_is_exact_digest_linked_and_bounded(
    tmp_path: Path, monkeypatch
):
    source = _large_compressed_fixture(tmp_path, 20_000)
    expected = source.read_bytes()
    requested_sizes: list[int] = []
    real_pread = viewer.os.pread

    def recording_pread(fd: int, size: int, offset: int) -> bytes:
        requested_sizes.append(size)
        return real_pread(fd, size, offset)

    monkeypatch.setattr(viewer.os, "pread", recording_pread)
    with _running_server(_allowlist(source)) as port:
        status, headers, body = _request(
            port, "GET", f"/api/viewer/source/{TEST_SCENE_ID}"
        )

    digest = hashlib.sha256(expected).hexdigest()
    assert status == 200
    assert body == expected
    assert headers["content-type"] == "application/octet-stream"
    assert headers["content-length"] == str(len(expected))
    assert headers["x-scene-sha256"] == digest
    assert headers["digest"].startswith("sha-256=")
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert "access-control-allow-origin" not in headers
    assert requested_sizes
    assert max(requested_sizes) <= 1024 * 1024
    assert min(requested_sizes) > 0


def test_create_server_copies_injected_allowlist(compressed_factory):
    source = compressed_factory(vertex_count=2)
    configured = _allowlist(source)
    with _running_server(configured) as port:
        configured.clear()
        status, _, payload = _json_request(
            port, "GET", f"/api/viewer/manifest?scene_id={TEST_SCENE_ID}"
        )
    assert status == 200
    assert payload["gaussian_count"] == 2


@pytest.mark.parametrize(
    ("target", "status", "error_type"),
    [
        ("/api/viewer/manifest", 400, "invalid_scene_id"),
        ("/api/viewer/manifest?", 400, "invalid_scene_id"),
        ("/api/viewer/manifest?scene_id=", 400, "invalid_scene_id"),
        (
            "/api/viewer/manifest?scene_id=fixture_scene&scene_id=fixture_scene",
            400,
            "invalid_scene_id",
        ),
        ("/api/viewer/manifest?other=fixture_scene", 400, "invalid_scene_id"),
        ("/api/viewer/manifest?scene_id=../labels.json", 400, "invalid_scene_id"),
        ("/api/viewer/manifest?scene_id=%2e%2e%2flabels.json", 400, "invalid_scene_id"),
        ("/api/viewer/manifest?scene_id=%00fixture", 400, "invalid_scene_id"),
        ("/api/viewer/manifest?scene_id=fixture%ZZ", 400, "invalid_scene_id"),
        ("/api/viewer/manifest?scene_id=unknown_scene", 404, "scene_unavailable"),
        ("/api/viewer/source", 400, "invalid_scene_id"),
        ("/api/viewer/source/", 400, "invalid_scene_id"),
        ("/api/viewer/source/%2e%2e%2flabels.json", 400, "invalid_scene_id"),
        ("/api/viewer/source/%5cetc%5cpasswd", 400, "invalid_scene_id"),
        ("/api/viewer/source/unknown_scene", 404, "scene_unavailable"),
    ],
)
def test_invalid_and_unknown_ids_are_controlled_and_redacted(
    compressed_factory, target: str, status: int, error_type: str
):
    source = compressed_factory(vertex_count=2)
    with _running_server(_allowlist(source)) as port:
        actual_status, headers, payload = _json_request(port, "GET", target)
    assert actual_status == status
    assert payload["ok"] is False
    assert payload["error"]["type"] == error_type
    serialized = json.dumps(payload)
    assert str(source) not in serialized
    assert "labels.json" not in serialized
    assert "access-control-allow-origin" not in headers
    assert headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("kind", ["symlink", "directory", "wrong_extension"])
def test_unsafe_injected_sources_are_rejected_during_configuration(
    compressed_factory, tmp_path: Path, kind: str
):
    regular = compressed_factory(vertex_count=2)
    if kind == "symlink":
        configured = tmp_path / "redirect.ply"
        configured.symlink_to(regular)
    elif kind == "directory":
        configured = tmp_path / "directory.ply"
        configured.mkdir()
    else:
        configured = tmp_path / "source.bin"
        configured.write_bytes(b"not exposed")

    with pytest.raises(
        ValueError, match="viewer_sources contains an unavailable or unsafe source"
    ):
        create_server(
            "127.0.0.1",
            0,
            repository_root=REPOSITORY_ROOT,
            viewer_sources=_allowlist(configured),
        )


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO creation is unavailable")
def test_non_regular_fifo_is_rejected_without_blocking(tmp_path: Path):
    fifo = tmp_path / "source.ply"
    os.mkfifo(fifo)
    with pytest.raises(
        ValueError, match="viewer_sources contains an unavailable or unsafe source"
    ):
        create_server(
            "127.0.0.1",
            0,
            repository_root=REPOSITORY_ROOT,
            viewer_sources=_allowlist(fifo),
        )


def test_injected_sources_require_explicit_trusted_coordinate_metadata(
    compressed_factory,
):
    source = compressed_factory(vertex_count=2)
    with pytest.raises(ValueError, match="ViewerSourceConfig"):
        create_server(
            "127.0.0.1",
            0,
            repository_root=REPOSITORY_ROOT,
            viewer_sources={TEST_SCENE_ID: source},  # type: ignore[dict-item]
        )
    unknown_coordinates = viewer.ViewerCoordinateSystem(
        world_up="unknown",
        floor_axes=("unknown", "unknown"),
        units="scene_units",
    )
    with pytest.raises(ValueError, match=r"explicit trusted \+Z"):
        create_server(
            "127.0.0.1",
            0,
            repository_root=REPOSITORY_ROOT,
            viewer_sources={
                TEST_SCENE_ID: viewer.ViewerSourceConfig(
                    path=source,
                    coordinate_system=unknown_coordinates,
                )
            },
        )


def test_pinned_canonical_target_ignores_later_ancestor_symlink_retarget(
    tmp_path: Path,
):
    original_directory = tmp_path / "original"
    alternate_directory = tmp_path / "alternate"
    original_directory.mkdir()
    alternate_directory.mkdir()
    original = _large_compressed_fixture(original_directory, 2)
    alternate = _large_compressed_fixture(alternate_directory, 2)
    _set_chunk_bounds(alternate, [(20, 20, 20, 30, 30, 30)])
    assert sha256_file(original) != sha256_file(alternate)
    alias = tmp_path / "active"
    alias.symlink_to(original_directory, target_is_directory=True)
    configured_alias = alias / original.name

    with _running_server(_allowlist(configured_alias)) as port:
        alias.unlink()
        alias.symlink_to(alternate_directory, target_is_directory=True)
        status, headers, body = _request(
            port, "GET", f"/api/viewer/source/{TEST_SCENE_ID}"
        )

    assert status == 200
    assert headers["x-scene-sha256"] == sha256_file(original)
    assert body == original.read_bytes()
    assert body != alternate.read_bytes()


@pytest.mark.parametrize("variant", ["malformed", "truncated", "nonfinite", "inverted"])
def test_invalid_compressed_sources_are_rejected_without_parser_details(
    compressed_factory, tmp_path: Path, variant: str
):
    if variant == "malformed":
        source = tmp_path / "sensitive-name.ply"
        source.write_bytes(b"not a PLY")
    else:
        source = compressed_factory(vertex_count=2)
        if variant == "truncated":
            with source.open("r+b") as handle:
                handle.truncate(source.stat().st_size - 1)
        elif variant == "nonfinite":
            _set_chunk_bounds(source, [(float("nan"), 0, 0, 1, 1, 1)])
        else:
            _set_chunk_bounds(source, [(2, 0, 0, 1, 1, 1)])

    with _running_server(_allowlist(source)) as port:
        status, _, payload = _json_request(
            port, "GET", f"/api/viewer/manifest?scene_id={TEST_SCENE_ID}"
        )
    assert status == 422
    assert payload == {
        "ok": False,
        "error": {
            "type": "invalid_viewer_source",
            "message": "viewer source is not a valid supported compressed PLY",
        },
    }
    assert str(source) not in json.dumps(payload)


@pytest.mark.parametrize(
    "variant",
    [
        "scale_nonfinite",
        "scale_inverted",
        "scale_exp_overflow",
        "color_nonfinite",
        "color_inverted",
        "color_decode_overflow",
        "packed_rotation",
    ],
)
def test_secondary_chunk_fields_and_decoded_values_are_validated(
    compressed_factory, variant: str
):
    source = compressed_factory(vertex_count=2)
    if variant == "scale_nonfinite":
        _set_chunk_float(source, 7, float("nan"))
    elif variant == "scale_inverted":
        _set_chunk_float(source, 6, 2.0)
    elif variant == "scale_exp_overflow":
        _set_chunk_float(source, 9, 100.0)
    elif variant == "color_nonfinite":
        _set_chunk_float(source, 16, float("inf"))
    elif variant == "color_inverted":
        _set_chunk_float(source, 12, 2.0)
    elif variant == "color_decode_overflow":
        _set_chunk_float(source, 15, 3.4028234663852886e38)
    else:
        _set_packed_rotation(source, 0)

    with _running_server(_allowlist(source)) as port:
        status, _, payload = _json_request(
            port, "GET", f"/api/viewer/manifest?scene_id={TEST_SCENE_ID}"
        )
    assert status == 422
    assert payload["error"]["type"] == "invalid_viewer_source"


@pytest.mark.parametrize("method", ["POST", "HEAD", "OPTIONS", "PUT", "DELETE"])
@pytest.mark.parametrize(
    "target",
    [
        f"/api/viewer/manifest?scene_id={TEST_SCENE_ID}",
        f"/api/viewer/source/{TEST_SCENE_ID}",
    ],
)
def test_viewer_routes_reject_non_get_methods(
    compressed_factory, method: str, target: str
):
    source = compressed_factory(vertex_count=2)
    with _running_server(_allowlist(source)) as port:
        status, headers, payload = _json_request(port, method, target)
    assert status == 405
    assert headers["allow"] == "GET"
    assert "access-control-allow-origin" not in headers
    if method != "HEAD":
        assert payload["error"]["type"] == "method_not_allowed"


def test_stream_aborts_before_content_length_if_source_changes(tmp_path: Path):
    source = _large_compressed_fixture(tmp_path, 40_000)
    sources = viewer.normalize_viewer_sources(_allowlist(source))

    class MutatingSink:
        def __init__(self) -> None:
            self.data = bytearray()
            self.mutated = False

        def write(self, block: bytes) -> int:
            self.data.extend(block)
            if not self.mutated:
                with source.open("r+b") as handle:
                    handle.seek(-1, os.SEEK_END)
                    original = handle.read(1)
                    handle.seek(-1, os.SEEK_END)
                    handle.write(bytes([original[0] ^ 1]))
                self.mutated = True
            return len(block)

    sink = MutatingSink()
    with open_viewer_stream(sources, TEST_SCENE_ID) as (opened, fingerprint):
        with pytest.raises(ViewerSourceChanged):
            opened.stream_to(sink, fingerprint)
    assert sink.mutated
    assert len(sink.data) < fingerprint.size_bytes


def test_http_stream_mutation_closes_before_declared_content_length(
    tmp_path: Path, monkeypatch
):
    source = _large_compressed_fixture(tmp_path, 40_000)
    first_chunk_written = threading.Event()
    continue_stream = threading.Event()
    original_stream_to = viewer.OpenViewerSource.stream_to

    def paused_stream_to(opened, destination, expected):
        class PausingDestination:
            def __init__(self) -> None:
                self.first_write = True

            def write(self, block: bytes) -> int:
                written = destination.write(block)
                if self.first_write:
                    self.first_write = False
                    first_chunk_written.set()
                    if not continue_stream.wait(timeout=10):
                        raise TimeoutError("test did not release paused viewer stream")
                return written

        return original_stream_to(opened, PausingDestination(), expected)

    monkeypatch.setattr(viewer.OpenViewerSource, "stream_to", paused_stream_to)
    connection: http.client.HTTPConnection | None = None
    try:
        with _running_server(_allowlist(source)) as port:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
            connection.request("GET", f"/api/viewer/source/{TEST_SCENE_ID}")
            response = connection.getresponse()
            declared_length = int(response.getheader("Content-Length", "0"))
            assert response.status == 200
            assert first_chunk_written.wait(timeout=10)
            with source.open("r+b") as handle:
                handle.seek(-1, os.SEEK_END)
                original = handle.read(1)
                handle.seek(-1, os.SEEK_END)
                handle.write(bytes([original[0] ^ 1]))
            continue_stream.set()
            with pytest.raises(http.client.IncompleteRead) as caught:
                response.read()
            assert len(caught.value.partial) < declared_length
    finally:
        continue_stream.set()
        if connection is not None:
            connection.close()


def test_viewer_concurrency_limit_returns_controlled_busy(compressed_factory):
    source = compressed_factory(vertex_count=2)
    server = create_server(
        "127.0.0.1",
        0,
        repository_root=REPOSITORY_ROOT,
        viewer_sources=_allowlist(source),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    acquired = server.viewer_operation_slots.acquire(blocking=False)  # type: ignore[attr-defined]
    assert acquired
    try:
        status, headers, payload = _json_request(
            int(server.server_port),
            "GET",
            f"/api/viewer/manifest?scene_id={TEST_SCENE_ID}",
        )
        assert status == 503
        assert payload == {
            "ok": False,
            "error": {
                "type": "viewer_busy",
                "message": "another viewer operation is already running",
            },
        }
        assert "access-control-allow-origin" not in headers
    finally:
        server.viewer_operation_slots.release()  # type: ignore[attr-defined]
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.skipif(not KNOWN_SOURCE.is_file(), reason="known InteriorGS source is unavailable")
def test_real_source_manifest_stream_and_fingerprint_are_exact():
    expected_sha256 = "c82a07ca1f2d4502df9dfb83e0b26973392e5139f78d3fe1879427c272b426da"
    before = (sha256_file(KNOWN_SOURCE), KNOWN_SOURCE.stat().st_size)
    assert before == (expected_sha256, 32_144_308)

    with _running_server(None) as port:
        status, _, manifest = _json_request(
            port,
            "GET",
            f"/api/viewer/manifest?scene_id={viewer.DEFAULT_SCENE_ID}",
        )
        assert status == 200
        assert manifest["gaussian_count"] == 524_508
        assert manifest["chunk_count"] == 2_049
        assert manifest["size_bytes"] == 32_144_308
        assert manifest["sha256"] == expected_sha256
        assert manifest["scene_aabb"]["min"] == pytest.approx(
            [-6.4937675035358176, -8.491147283351491, -5.602591798801276],
            abs=1e-12,
        )
        assert manifest["scene_aabb"]["max"] == pytest.approx(
            [6.81342827358616, 8.76070222006658, 5.533811335966632],
            abs=1e-12,
        )

        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        digest = hashlib.sha256()
        streamed_size = 0
        try:
            connection.request(
                "GET", f"/api/viewer/source/{viewer.DEFAULT_SCENE_ID}"
            )
            response = connection.getresponse()
            assert response.status == 200
            assert response.getheader("Content-Length") == "32144308"
            assert response.getheader("X-Scene-SHA256") == expected_sha256
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                digest.update(block)
                streamed_size += len(block)
        finally:
            connection.close()
    assert streamed_size == 32_144_308
    assert digest.hexdigest() == expected_sha256
    assert (sha256_file(KNOWN_SOURCE), KNOWN_SOURCE.stat().st_size) == before


@pytest.mark.skipif(not KNOWN_SOURCE.is_file(), reason="known InteriorGS source is unavailable")
def test_real_manifest_and_stream_rss_delta_stays_within_m1_budget():
    script = """
import json
import resource
from scene_agent.viewer import (
    DEFAULT_SCENE_ID,
    build_viewer_manifest,
    normalize_viewer_sources,
    open_viewer_stream,
)

class Sink:
    def write(self, block):
        return len(block)

sources = normalize_viewer_sources(None)
before_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
manifest = build_viewer_manifest(sources, DEFAULT_SCENE_ID)
with open_viewer_stream(sources, DEFAULT_SCENE_ID) as (source, fingerprint):
    source.stream_to(Sink(), fingerprint)
after_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
print(json.dumps({
    "rss_delta_bytes": max(0, after_kib - before_kib) * 1024,
    "sha256": manifest.fingerprint.sha256,
    "size_bytes": manifest.fingerprint.size_bytes,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    measurement = json.loads(completed.stdout)
    assert measurement["sha256"] == (
        "c82a07ca1f2d4502df9dfb83e0b26973392e5139f78d3fe1879427c272b426da"
    )
    assert measurement["size_bytes"] == 32_144_308
    assert measurement["rss_delta_bytes"] <= 32 * 1024 * 1024
