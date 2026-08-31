"""Deterministic and security contracts for M3 reference capture."""

from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import zlib

import pytest

import scene_agent.capture as capture_module
from scene_agent.capture import (
    CAMERA_ALGORITHM,
    CaptureOutputError,
    InvalidCapture,
    InvalidCapturePNG,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    ViewerSessionStore,
    canonical_json_bytes,
    compute_zup_aabb_v1,
    decode_capture_payload,
    deterministic_camera_digest,
    deterministic_capture_projection,
    save_reference_capture,
    validate_capture_metadata,
    validate_reference_png,
)
from scene_agent.viewer import (
    ViewerFingerprint,
    ViewerSourceConfig,
    Z_UP_COORDINATE_SYSTEM,
    build_viewer_manifest,
    normalize_viewer_sources,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _reference_png(*, width: int = REFERENCE_WIDTH, height: int = REFERENCE_HEIGHT) -> bytes:
    row = b"\x00" + b"\x00\x00\x00\xff" * width
    pixels = row * height
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(pixels, level=9))
        + _png_chunk(b"IEND", b"")
    )


def _manifest_and_sources(compressed_factory):
    source = compressed_factory(vertex_count=4)
    sources = normalize_viewer_sources(
        {
            "capture_scene": ViewerSourceConfig(
                path=source,
                coordinate_system=Z_UP_COORDINATE_SYSTEM,
            )
        }
    )
    return source, sources, build_viewer_manifest(sources, "capture_scene")


@pytest.mark.parametrize(
    ("aabb", "width", "height"),
    [
        ({"min": [-6.5, -8.5, -5.6], "max": [6.8, 8.7, 5.5]}, 1280, 720),
        ({"min": [-9.0, -4.0, -2.0], "max": [-1.0, 3.0, 6.0]}, 600, 1000),
        ({"min": [1.0, 2.0, 3.0], "max": [1.0, 8.0, 9.0]}, 1280, 720),
        ({"min": [1.0, 2.0, 3.0], "max": [7.0, 2.0, 9.0]}, 1280, 720),
        ({"min": [1.0, 2.0, 3.0], "max": [7.0, 8.0, 3.0]}, 1280, 720),
        ({"min": [1.0, 2.0, 3.0], "max": [1.0, 2.0, 9.0]}, 1280, 720),
        ({"min": [1.0, 2.0, 3.0], "max": [1.0, 8.0, 3.0]}, 1280, 720),
        ({"min": [1.0, 2.0, 3.0], "max": [7.0, 2.0, 3.0]}, 1280, 720),
    ],
)
def test_python_javascript_zup_aabb_v1_parity(aabb, width: int, height: int):
    expected = compute_zup_aabb_v1(aabb, width=width, height=height)
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable for camera parity")
    script = f"""
import {{ zupAabbCamera }} from './src/scene_agent/web/viewer-camera.js';
console.log(JSON.stringify(zupAabbCamera({json.dumps(aabb)}, {width}, {height})));
"""
    completed = subprocess.run(
        [node, "--input-type=module", "-e", script],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == expected
    assert expected["projection"] == "orthographic"
    assert expected["view_up"] == [0.0, 1.0, 0.0]
    assert expected["viewport_px"] == [width, height]
    assert expected["far"] > expected["near"] > 0
    assert all(
        value == value and abs(value) < float("inf")
        for key in ("position", "target", "view_up")
        for value in expected[key]
    )


def test_vertical_only_partial_degeneracy_uses_fixed_epsilon_span():
    camera = compute_zup_aabb_v1(
        {"min": [4.0, -2.0, 1.0], "max": [4.0, -2.0, 8.0]}
    )
    assert camera["orthographic_height"] == 1.10e-6 / 2
    assert camera["position"] == [4.0, -2.0, 22.0]
    assert camera["target"] == [4.0, -2.0, 4.5]


@pytest.mark.parametrize("axis", range(3))
def test_zup_aabb_v1_rejects_each_inverted_axis(axis: int):
    minimum = [0.0, 0.0, 0.0]
    maximum = [1.0, 1.0, 1.0]
    minimum[axis] = 2.0
    with pytest.raises(InvalidCapture, match="inverted"):
        compute_zup_aabb_v1({"min": minimum, "max": maximum})


@pytest.mark.parametrize("side", ["min", "max"])
@pytest.mark.parametrize("axis", range(3))
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_zup_aabb_v1_rejects_every_nonfinite_bound(side: str, axis: int, value: float):
    aabb = {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]}
    aabb[side][axis] = value
    with pytest.raises(InvalidCapture, match="finite"):
        compute_zup_aabb_v1(aabb)


def test_zup_aabb_v1_rejects_full_degeneracy_and_overflow_before_camera():
    with pytest.raises(InvalidCapture, match="fully degenerate"):
        compute_zup_aabb_v1({"min": [3.0, 3.0, 3.0], "max": [3.0, 3.0, 3.0]})
    with pytest.raises(InvalidCapture, match="derived values"):
        compute_zup_aabb_v1(
            {"min": [-1e308, 0.0, 0.0], "max": [1e308, 1.0, 1.0]}
        )


def test_projection_and_digest_are_canonical_and_repeatable(compressed_factory):
    _source, _sources, manifest = _manifest_and_sources(compressed_factory)
    first = deterministic_capture_projection(manifest)
    second = deterministic_capture_projection(manifest)
    encoded = canonical_json_bytes(first)

    assert first == second
    assert encoded == canonical_json_bytes(second)
    assert not encoded.endswith(b"\n")
    assert deterministic_camera_digest(first) == deterministic_camera_digest(second)
    assert first["camera"]["viewport_px"] == [1280, 720]
    assert first["camera"]["pixel_ratio"] == 1
    assert first["capture"] == {
        "view_kind": "birdseye",
        "camera_algorithm": CAMERA_ALGORITHM,
    }


def _invalid_metadata_cases(expected: dict[str, object]):
    cases = []
    for path, value in (
        (("schema_version",), 2),
        (("source", "sha256"), "0" * 64),
        (("source", "gaussian_count"), 5),
        (("coordinate_system", "world_up"), "+Y"),
        (("camera", "projection"), "perspective"),
        (("camera", "position"), [0, 0, 0]),
        (("camera", "near"), 0.25),
        (("camera", "viewport_px"), [720, 1280]),
        (("camera", "pixel_ratio"), 2),
        (("render_config", "renderer"), "custom"),
        (("render_config", "background_rgba"), [1, 1, 1, 1]),
        (("capture", "view_kind"), "current"),
        (("capture", "camera_algorithm"), "manual"),
    ):
        metadata = deepcopy(expected)
        target = metadata
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        cases.append(metadata)
    extra = deepcopy(expected)
    extra["diagnostics"] = {"browser": "untrusted"}
    cases.append(extra)
    nonfinite = deepcopy(expected)
    nonfinite["camera"]["far"] = float("nan")
    cases.append(nonfinite)
    boolean = deepcopy(expected)
    boolean["render_config"]["background_rgba"] = [False, 0, 0, True]
    cases.append(boolean)
    return cases


def test_capture_metadata_rejects_wrong_schema_values_and_nonfinite(compressed_factory):
    _source, _sources, manifest = _manifest_and_sources(compressed_factory)
    expected = deterministic_capture_projection(manifest)
    validate_capture_metadata(deepcopy(expected), expected)
    for invalid in _invalid_metadata_cases(expected):
        with pytest.raises(InvalidCapture):
            validate_capture_metadata(invalid, expected)


def test_png_and_base64_validation_is_strict():
    png = _reference_png()
    validate_reference_png(png)
    decoded, metadata = decode_capture_payload(
        {"png_base64": base64.b64encode(png).decode("ascii"), "metadata": {}}
    )
    assert decoded == png
    assert metadata == {}

    invalid = [
        b"not png",
        _reference_png(width=1279),
        png + b"trailing",
        png[:-12],
        png[:20] + bytes([png[20] ^ 1]) + png[21:],
    ]
    for value in invalid:
        with pytest.raises(InvalidCapturePNG):
            validate_reference_png(value)
    with pytest.raises(InvalidCapturePNG, match="strict base64"):
        decode_capture_payload({"png_base64": "%%%%", "metadata": {}})


def test_valid_capture_writes_exact_atomic_pairs_and_repeatable_digest(
    tmp_path: Path,
    compressed_factory,
):
    _source, sources, manifest = _manifest_and_sources(compressed_factory)
    metadata = deterministic_capture_projection(manifest)
    png = _reference_png()

    first = save_reference_capture(
        tmp_path,
        sources,
        scene_id="capture_scene",
        png=png,
        client_metadata=metadata,
    )
    second = save_reference_capture(
        tmp_path,
        sources,
        scene_id="capture_scene",
        png=png,
        client_metadata=metadata,
    )

    assert first.capture_id != second.capture_id
    assert first.deterministic_camera_digest == second.deterministic_camera_digest
    capture_root = tmp_path / "outputs" / "viewer"
    assert sorted(path.name for path in capture_root.iterdir()) == sorted(
        [first.capture_id, second.capture_id]
    )
    for result in (first, second):
        directory = capture_root / result.capture_id
        assert sorted(path.name for path in directory.iterdir()) == ["camera.json", "screenshot.png"]
        assert (directory / "screenshot.png").read_bytes() == png
        raw_camera = (directory / "camera.json").read_bytes()
        assert raw_camera.endswith(b"\n") and not raw_camera.endswith(b"\n\n")
        stored = json.loads(raw_camera)
        assert stored["deterministic_camera_digest"] == result.deterministic_camera_digest
        projection = {key: value for key, value in stored.items() if key != "deterministic_camera_digest"}
        assert deterministic_camera_digest(projection) == result.deterministic_camera_digest
        assert all(not Path(path).is_absolute() for path in (result.screenshot_path, result.camera_path))


def test_invalid_capture_and_symlink_output_create_no_pair(
    tmp_path: Path,
    compressed_factory,
):
    _source, sources, manifest = _manifest_and_sources(compressed_factory)
    invalid = deterministic_capture_projection(manifest)
    invalid["camera"]["projection"] = "perspective"
    with pytest.raises(InvalidCapture):
        save_reference_capture(
            tmp_path,
            sources,
            scene_id="capture_scene",
            png=_reference_png(),
            client_metadata=invalid,
        )
    assert not (tmp_path / "outputs").exists()

    external = tmp_path / "external"
    external.mkdir()
    (tmp_path / "outputs").symlink_to(external, target_is_directory=True)
    valid = deterministic_capture_projection(manifest)
    with pytest.raises(CaptureOutputError):
        save_reference_capture(
            tmp_path,
            sources,
            scene_id="capture_scene",
            png=_reference_png(),
            client_metadata=valid,
        )
    assert list(external.iterdir()) == []


def test_source_revalidation_failure_cleans_owned_temporary_pair(
    tmp_path: Path,
    compressed_factory,
    monkeypatch,
):
    _source, sources, manifest = _manifest_and_sources(compressed_factory)
    changed = replace(
        manifest,
        fingerprint=ViewerFingerprint(
            sha256="f" * 64,
            size_bytes=manifest.fingerprint.size_bytes,
        ),
    )
    manifests = iter((manifest, changed))
    monkeypatch.setattr(capture_module, "build_viewer_manifest", lambda *_args: next(manifests))

    with pytest.raises(Exception, match="source changed"):
        save_reference_capture(
            tmp_path,
            sources,
            scene_id="capture_scene",
            png=_reference_png(),
            client_metadata=deterministic_capture_projection(manifest),
        )
    viewer_root = tmp_path / "outputs" / "viewer"
    assert viewer_root.is_dir()
    assert list(viewer_root.iterdir()) == []


def test_viewer_session_tokens_are_bounded_bound_and_expiring(monkeypatch):
    now = [10.0]
    tokens = iter(("a" * 43, "b" * 43, "c" * 43))
    monkeypatch.setattr(capture_module.secrets, "token_urlsafe", lambda _size: next(tokens))
    store = ViewerSessionStore(ttl_seconds=5, max_sessions=2, clock=lambda: now[0])
    first, ttl = store.issue(origin="http://127.0.0.1:8765", client="127.0.0.1")
    second, _ = store.issue(origin="http://127.0.0.1:8765", client="127.0.0.1")

    assert ttl == 5
    assert store.validate(first, origin="http://127.0.0.1:8765", client="127.0.0.1")
    assert not store.validate(first, origin="http://localhost:8765", client="127.0.0.1")
    assert not store.validate(first, origin="http://127.0.0.1:8765", client="::1")
    store.issue(origin="http://127.0.0.1:8765", client="127.0.0.1")
    assert not store.validate(first, origin="http://127.0.0.1:8765", client="127.0.0.1")
    assert store.validate(second, origin="http://127.0.0.1:8765", client="127.0.0.1")
    now[0] = 16.0
    assert not store.validate(second, origin="http://127.0.0.1:8765", client="127.0.0.1")
    assert not store.validate("malformed", origin="http://127.0.0.1:8765", client="127.0.0.1")
