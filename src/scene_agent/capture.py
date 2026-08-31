"""Deterministic and confined bird's-eye reference capture contracts."""

from __future__ import annotations

from dataclasses import dataclass
import base64
import binascii
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import struct
import threading
import time
from typing import Callable, Mapping
import zlib

from .viewer import (
    PinnedViewerSource,
    ViewerManifest,
    ViewerSourceChanged,
    build_viewer_manifest,
)


CAPTURE_SCHEMA_VERSION = 1
CAMERA_ALGORITHM = "zup_aabb_v1"
REFERENCE_WIDTH = 1280
REFERENCE_HEIGHT = 720
REFERENCE_PIXEL_RATIO = 1
REFERENCE_MARGIN = 1.10
REFERENCE_EPSILON = 1e-6
MAX_CAPTURE_BODY_BYTES = 24 * 1024 * 1024
MAX_PNG_BYTES = 16 * 1024 * 1024
MAX_CONCURRENT_CAPTURE_WRITES = 1
VIEWER_SESSION_TTL_SECONDS = 300
MAX_VIEWER_SESSIONS = 64

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_CAPTURE_ID = re.compile(r"^capture_[a-f0-9]{24}$")
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_FILE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW


class CaptureError(Exception):
    """Base class for controlled reference-capture failures."""


class InvalidCapture(CaptureError):
    """Client payload is not the exact deterministic reference contract."""


class InvalidCapturePNG(CaptureError):
    """Client PNG is malformed or outside the fixed capture contract."""


class CaptureOutputError(CaptureError):
    """The confined output pair could not be published safely."""


@dataclass(frozen=True)
class CaptureResult:
    capture_id: str
    screenshot_path: str
    camera_path: str
    deterministic_camera_digest: str

    def as_dict(self) -> dict[str, str]:
        return {
            "capture_id": self.capture_id,
            "screenshot_path": self.screenshot_path,
            "camera_path": self.camera_path,
            "deterministic_camera_digest": self.deterministic_camera_digest,
        }


@dataclass(frozen=True)
class _ViewerSession:
    token: str
    origin: str
    client: str
    expires_at: float


class ViewerSessionStore:
    """Small, bounded, server-local store for short-lived capture tokens."""

    def __init__(
        self,
        *,
        ttl_seconds: int = VIEWER_SESSION_TTL_SECONDS,
        max_sessions: int = MAX_VIEWER_SESSIONS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(ttl_seconds, int) or isinstance(ttl_seconds, bool) or ttl_seconds <= 0:
            raise ValueError("viewer session TTL must be a positive integer")
        if not isinstance(max_sessions, int) or isinstance(max_sessions, bool) or max_sessions <= 0:
            raise ValueError("viewer session capacity must be a positive integer")
        self.ttl_seconds = ttl_seconds
        self.max_sessions = max_sessions
        self._clock = clock
        self._sessions: list[_ViewerSession] = []
        self._lock = threading.Lock()

    def _purge_locked(self, now: float) -> None:
        self._sessions = [session for session in self._sessions if session.expires_at > now]

    def issue(self, *, origin: str, client: str) -> tuple[str, int]:
        if not isinstance(origin, str) or not origin or len(origin) > 512:
            raise ValueError("viewer session origin is invalid")
        if not isinstance(client, str) or not client or len(client) > 128:
            raise ValueError("viewer session client is invalid")
        token = secrets.token_urlsafe(32)
        if _TOKEN.fullmatch(token) is None:
            raise RuntimeError("secure token generator returned an invalid token")
        now = self._clock()
        with self._lock:
            self._purge_locked(now)
            if len(self._sessions) >= self.max_sessions:
                self._sessions.pop(0)
            self._sessions.append(
                _ViewerSession(
                    token=token,
                    origin=origin,
                    client=client,
                    expires_at=now + self.ttl_seconds,
                )
            )
        return token, self.ttl_seconds

    def validate(self, token: object, *, origin: str, client: str) -> bool:
        if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
            return False
        now = self._clock()
        matched: _ViewerSession | None = None
        with self._lock:
            self._purge_locked(now)
            for session in self._sessions:
                if hmac.compare_digest(session.token, token):
                    matched = session
        return (
            matched is not None
            and matched.expires_at > now
            and hmac.compare_digest(matched.origin, origin)
            and hmac.compare_digest(matched.client, client)
        )


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidCapture(f"{label} must be a finite number")
    converted = float(value)
    if not math.isfinite(converted):
        raise InvalidCapture(f"{label} must be a finite number")
    return converted


def _finite_vector(value: object, label: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise InvalidCapture(f"{label} must contain three finite numbers")
    return tuple(_finite_number(item, f"{label}[{index}]") for index, item in enumerate(value))


def compute_zup_aabb_v1(
    scene_aabb: Mapping[str, object],
    *,
    width: int = REFERENCE_WIDTH,
    height: int = REFERENCE_HEIGHT,
) -> dict[str, object]:
    """Compute the exact finite Z-up orthographic camera defined by the viewer spec."""

    if not isinstance(scene_aabb, Mapping):
        raise InvalidCapture("scene AABB must be an object")
    if set(scene_aabb) != {"min", "max"}:
        raise InvalidCapture("scene AABB must contain exactly min and max")
    minimum = _finite_vector(scene_aabb["min"], "scene_aabb.min")
    maximum = _finite_vector(scene_aabb["max"], "scene_aabb.max")
    for index in range(3):
        if minimum[index] > maximum[index]:
            raise InvalidCapture("scene AABB is inverted")
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, int)
        or not isinstance(height, int)
        or width <= 0
        or height <= 0
    ):
        raise InvalidCapture("camera viewport must use positive integer dimensions")

    extents = tuple(maximum[index] - minimum[index] for index in range(3))
    center = tuple(minimum[index] + extents[index] / 2 for index in range(3))
    if not all(math.isfinite(value) for value in (*extents, *center)):
        raise InvalidCapture("scene AABB derived values are non-finite")
    if all(value == 0 for value in extents):
        raise InvalidCapture("scene AABB is fully degenerate")

    aspect = width / height
    span_y = REFERENCE_MARGIN * max(extents[1], extents[0] / aspect, REFERENCE_EPSILON)
    orthographic_height = span_y / 2
    clearance = max(*extents, 1.0)
    z_camera = maximum[2] + 2 * clearance
    near = max(1e-4, z_camera - maximum[2] - clearance / 2)
    far = z_camera - minimum[2] + clearance / 2
    derived = (aspect, span_y, orthographic_height, clearance, z_camera, near, far)
    if not all(math.isfinite(value) for value in derived) or far <= near:
        raise InvalidCapture("deterministic bird's-eye camera is non-finite")

    return {
        "projection": "orthographic",
        "position": [center[0], center[1], z_camera],
        "target": list(center),
        "view_up": [0.0, 1.0, 0.0],
        "near": near,
        "far": far,
        "orthographic_height": orthographic_height,
        "viewport_px": [width, height],
        "pixel_ratio": REFERENCE_PIXEL_RATIO,
    }


def deterministic_capture_projection(manifest: ViewerManifest) -> dict[str, object]:
    manifest_values = manifest.as_dict()
    return {
        "schema_version": CAPTURE_SCHEMA_VERSION,
        "source": {
            key: manifest_values[key]
            for key in ("scene_id", "sha256", "size_bytes", "gaussian_count", "format")
        },
        "coordinate_system": manifest_values["coordinate_system"],
        "camera": compute_zup_aabb_v1(manifest_values["scene_aabb"]),
        "render_config": {
            "renderer": "playcanvas-official-gaussian",
            "playcanvas_version": "2.3.3",
            "background_rgba": [0, 0, 0, 1],
        },
        "capture": {
            "view_kind": "birdseye",
            "camera_algorithm": CAMERA_ALGORITHM,
        },
    }


def canonical_json_bytes(value: Mapping[str, object]) -> bytes:
    """Canonical compact UTF-8 bytes; stored files add one LF after these bytes."""

    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InvalidCapture("capture metadata is not canonical JSON") from exc


def deterministic_camera_digest(projection: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json_bytes(projection)).hexdigest()


def _require_keys(value: object, expected: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise InvalidCapture(f"{label} fields do not match schema version 1")
    return value


def _require_string(value: object, expected: str, label: str) -> None:
    if not isinstance(value, str) or len(value) > 128 or not hmac.compare_digest(value, expected):
        raise InvalidCapture(f"{label} does not match the trusted reference")


def _require_integer(value: object, expected: int, label: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value != expected:
        raise InvalidCapture(f"{label} does not match the trusted reference")


def _require_number(value: object, expected: float, label: str) -> None:
    actual = _finite_number(value, label)
    if actual != expected:
        raise InvalidCapture(f"{label} does not match zup_aabb_v1")


def _validate_vector(value: object, expected: list[float], label: str) -> None:
    if not isinstance(value, list) or len(value) != len(expected):
        raise InvalidCapture(f"{label} does not match zup_aabb_v1")
    for index, expected_value in enumerate(expected):
        _require_number(value[index], expected_value, f"{label}[{index}]")


def validate_capture_metadata(client: object, expected: Mapping[str, object]) -> None:
    metadata = _require_keys(
        client,
        {"schema_version", "source", "coordinate_system", "camera", "render_config", "capture"},
        "capture metadata",
    )
    _require_integer(metadata["schema_version"], CAPTURE_SCHEMA_VERSION, "schema_version")

    source_expected = expected["source"]
    source = _require_keys(
        metadata["source"],
        {"scene_id", "sha256", "size_bytes", "gaussian_count", "format"},
        "source",
    )
    for key in ("scene_id", "sha256", "format"):
        _require_string(source[key], source_expected[key], f"source.{key}")
    for key in ("size_bytes", "gaussian_count"):
        _require_integer(source[key], source_expected[key], f"source.{key}")

    coordinate_expected = expected["coordinate_system"]
    coordinate = _require_keys(
        metadata["coordinate_system"],
        {"world_up", "floor_axes", "units"},
        "coordinate_system",
    )
    _require_string(coordinate["world_up"], coordinate_expected["world_up"], "coordinate_system.world_up")
    _require_string(coordinate["units"], coordinate_expected["units"], "coordinate_system.units")
    if coordinate["floor_axes"] != coordinate_expected["floor_axes"]:
        raise InvalidCapture("coordinate_system.floor_axes does not match the trusted reference")

    camera_expected = expected["camera"]
    camera = _require_keys(
        metadata["camera"],
        {
            "projection",
            "position",
            "target",
            "view_up",
            "near",
            "far",
            "orthographic_height",
            "viewport_px",
            "pixel_ratio",
        },
        "camera",
    )
    _require_string(camera["projection"], "orthographic", "camera.projection")
    for key in ("position", "target", "view_up"):
        _validate_vector(camera[key], camera_expected[key], f"camera.{key}")
    for key in ("near", "far", "orthographic_height"):
        _require_number(camera[key], camera_expected[key], f"camera.{key}")
    if camera["viewport_px"] != [REFERENCE_WIDTH, REFERENCE_HEIGHT]:
        raise InvalidCapture("camera.viewport_px must be the fixed 1280x720 reference")
    _require_integer(camera["pixel_ratio"], REFERENCE_PIXEL_RATIO, "camera.pixel_ratio")

    render_expected = expected["render_config"]
    render = _require_keys(
        metadata["render_config"],
        {"renderer", "playcanvas_version", "background_rgba"},
        "render_config",
    )
    for key in ("renderer", "playcanvas_version"):
        _require_string(render[key], render_expected[key], f"render_config.{key}")
    background = render["background_rgba"]
    if not isinstance(background, list) or len(background) != 4:
        raise InvalidCapture("render_config.background_rgba must be fixed black")
    for index, expected_value in enumerate((0, 0, 0, 1)):
        _require_integer(
            background[index],
            expected_value,
            f"render_config.background_rgba[{index}]",
        )

    capture = _require_keys(metadata["capture"], {"view_kind", "camera_algorithm"}, "capture")
    _require_string(capture["view_kind"], "birdseye", "capture.view_kind")
    _require_string(capture["camera_algorithm"], CAMERA_ALGORITHM, "capture.camera_algorithm")


def decode_capture_payload(payload: object) -> tuple[bytes, dict[str, object]]:
    body = _require_keys(payload, {"png_base64", "metadata"}, "capture request")
    encoded = body["png_base64"]
    if not isinstance(encoded, str):
        raise InvalidCapturePNG("png_base64 must be a string")
    maximum_encoded = 4 * ((MAX_PNG_BYTES + 2) // 3)
    if not encoded or len(encoded) > maximum_encoded or not encoded.isascii():
        raise InvalidCapturePNG("encoded PNG exceeds the fixed capture limit")
    try:
        png = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidCapturePNG("png_base64 is not strict base64") from exc
    validate_reference_png(png)
    metadata = body["metadata"]
    if not isinstance(metadata, dict):
        raise InvalidCapture("metadata must be a JSON object")
    return png, metadata


def validate_reference_png(png: bytes) -> None:
    if not isinstance(png, bytes) or not png or len(png) > MAX_PNG_BYTES:
        raise InvalidCapturePNG("decoded PNG exceeds the fixed capture limit")
    if not png.startswith(_PNG_SIGNATURE):
        raise InvalidCapturePNG("capture is not a PNG")

    offset = len(_PNG_SIGNATURE)
    seen_ihdr = False
    seen_idat = False
    idat_ended = False
    seen_iend = False
    palette_seen = False
    idat = bytearray()
    image: tuple[int, int, int, int, int] | None = None
    while offset < len(png):
        if len(png) - offset < 12:
            raise InvalidCapturePNG("PNG chunk is truncated")
        length = struct.unpack(">I", png[offset : offset + 4])[0]
        chunk_type = png[offset + 4 : offset + 8]
        end = offset + 12 + length
        if length > MAX_PNG_BYTES or end > len(png):
            raise InvalidCapturePNG("PNG chunk length is invalid")
        data = png[offset + 8 : offset + 8 + length]
        expected_crc = struct.unpack(">I", png[offset + 8 + length : end])[0]
        if zlib.crc32(chunk_type + data) & 0xFFFFFFFF != expected_crc:
            raise InvalidCapturePNG("PNG chunk checksum is invalid")
        if not all(65 <= value <= 90 or 97 <= value <= 122 for value in chunk_type):
            raise InvalidCapturePNG("PNG chunk type is invalid")
        if not seen_ihdr and chunk_type != b"IHDR":
            raise InvalidCapturePNG("PNG IHDR must be first")
        if chunk_type == b"IHDR":
            if seen_ihdr or length != 13:
                raise InvalidCapturePNG("PNG IHDR is invalid")
            width, height, bit_depth, color_type, compression, filter_method, interlace = struct.unpack(
                ">IIBBBBB", data
            )
            if (width, height) != (REFERENCE_WIDTH, REFERENCE_HEIGHT):
                raise InvalidCapturePNG("PNG must be exactly 1280x720")
            legal_depths = {
                0: {1, 2, 4, 8, 16},
                2: {8, 16},
                3: {1, 2, 4, 8},
                4: {8, 16},
                6: {8, 16},
            }
            if bit_depth not in legal_depths.get(color_type, set()):
                raise InvalidCapturePNG("PNG color type and bit depth are invalid")
            if compression != 0 or filter_method != 0 or interlace != 0:
                raise InvalidCapturePNG("PNG compression, filter, or interlace is unsupported")
            image = (width, height, bit_depth, color_type, interlace)
            seen_ihdr = True
        elif chunk_type == b"PLTE":
            if seen_idat or palette_seen or length == 0 or length % 3 or length > 768:
                raise InvalidCapturePNG("PNG palette is invalid")
            palette_seen = True
        elif chunk_type == b"IDAT":
            if idat_ended or length == 0:
                raise InvalidCapturePNG("PNG IDAT sequence is invalid")
            seen_idat = True
            idat.extend(data)
            if len(idat) > MAX_PNG_BYTES:
                raise InvalidCapturePNG("PNG compressed pixels exceed the fixed limit")
        elif chunk_type == b"IEND":
            if length != 0 or not seen_idat or seen_iend:
                raise InvalidCapturePNG("PNG IEND is invalid")
            seen_iend = True
            offset = end
            if offset != len(png):
                raise InvalidCapturePNG("PNG has trailing data")
            break
        else:
            if seen_idat:
                idat_ended = True
            if chunk_type[0] & 0x20 == 0:
                raise InvalidCapturePNG("PNG contains an unknown critical chunk")
        offset = end

    if not seen_ihdr or not seen_idat or not seen_iend or image is None:
        raise InvalidCapturePNG("PNG is incomplete")
    width, height, bit_depth, color_type, _interlace = image
    if color_type == 3 and not palette_seen:
        raise InvalidCapturePNG("indexed PNG is missing its palette")
    if color_type in {0, 4} and palette_seen:
        raise InvalidCapturePNG("grayscale PNG cannot contain a palette")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    row_bytes = (width * channels * bit_depth + 7) // 8
    expected_size = height * (row_bytes + 1)
    inflater = zlib.decompressobj()
    try:
        pixels = inflater.decompress(bytes(idat), expected_size + 1)
        if len(pixels) > expected_size:
            raise InvalidCapturePNG("PNG pixel stream has an invalid decoded length")
        pixels += inflater.flush(expected_size + 1 - len(pixels))
    except zlib.error as exc:
        raise InvalidCapturePNG("PNG pixel stream is invalid") from exc
    if (
        len(pixels) != expected_size
        or not inflater.eof
        or inflater.unused_data
        or inflater.unconsumed_tail
    ):
        raise InvalidCapturePNG("PNG pixel stream has an invalid decoded length")
    if any(pixels[row * (row_bytes + 1)] > 4 for row in range(height)):
        raise InvalidCapturePNG("PNG row filter is invalid")


def _open_or_create_directory(parent_fd: int, name: str) -> int:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    try:
        descriptor = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        raise CaptureOutputError("viewer output directory is unavailable or unsafe") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise CaptureOutputError("viewer output directory is unavailable or unsafe")
    return descriptor


def _write_all(descriptor: int, content: bytes) -> None:
    offset = 0
    while offset < len(content):
        written = os.write(descriptor, content[offset : offset + 1024 * 1024])
        if written <= 0:
            raise CaptureOutputError("capture output write did not complete")
        offset += written
    os.fsync(descriptor)


def _same_manifest(left: ViewerManifest, right: ViewerManifest) -> bool:
    return canonical_json_bytes(left.as_dict()) == canonical_json_bytes(right.as_dict())


def save_reference_capture(
    repository_root: Path,
    viewer_sources: Mapping[str, PinnedViewerSource],
    *,
    scene_id: str,
    png: bytes,
    client_metadata: object,
) -> CaptureResult:
    """Validate and atomically publish one non-overwriting PNG/JSON pair."""

    validate_reference_png(png)
    before = build_viewer_manifest(viewer_sources, scene_id)
    projection = deterministic_capture_projection(before)
    validate_capture_metadata(client_metadata, projection)
    digest = deterministic_camera_digest(projection)
    stored_metadata = {**projection, "deterministic_camera_digest": digest}
    camera_bytes = canonical_json_bytes(stored_metadata) + b"\n"

    root = repository_root.resolve(strict=True)
    if not root.is_dir():
        raise CaptureOutputError("repository root is unavailable")
    root_fd = os.open(root, _DIRECTORY_FLAGS)
    outputs_fd = viewer_fd = temp_fd = None
    temp_name: str | None = None
    published = False
    try:
        outputs_fd = _open_or_create_directory(root_fd, "outputs")
        viewer_fd = _open_or_create_directory(outputs_fd, "viewer")
        capture_id = ""
        for _attempt in range(8):
            candidate = f"capture_{secrets.token_hex(12)}"
            if _CAPTURE_ID.fullmatch(candidate) is None:
                raise CaptureOutputError("secure capture identifier generation failed")
            try:
                os.stat(candidate, dir_fd=viewer_fd, follow_symlinks=False)
            except FileNotFoundError:
                capture_id = candidate
                break
        if not capture_id:
            raise CaptureOutputError("a unique capture identifier could not be allocated")

        temp_name = f".{capture_id}.tmp.{secrets.token_hex(8)}"
        os.mkdir(temp_name, mode=0o700, dir_fd=viewer_fd)
        temp_fd = os.open(temp_name, _DIRECTORY_FLAGS, dir_fd=viewer_fd)
        for filename, content in (("screenshot.png", png), ("camera.json", camera_bytes)):
            descriptor = os.open(filename, _FILE_FLAGS, mode=0o600, dir_fd=temp_fd)
            try:
                _write_all(descriptor, content)
            finally:
                os.close(descriptor)
        os.fsync(temp_fd)

        after = build_viewer_manifest(viewer_sources, scene_id)
        if not _same_manifest(before, after):
            raise ViewerSourceChanged("viewer source changed during capture")
        try:
            os.stat(capture_id, dir_fd=viewer_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise CaptureOutputError("capture identifier already exists")
        os.rename(temp_name, capture_id, src_dir_fd=viewer_fd, dst_dir_fd=viewer_fd)
        os.fsync(viewer_fd)
        published = True
    except ViewerSourceChanged:
        raise
    except CaptureError:
        raise
    except OSError as exc:
        raise CaptureOutputError("capture output could not be published safely") from exc
    finally:
        if not published and temp_fd is not None:
            for filename in ("screenshot.png", "camera.json"):
                try:
                    os.unlink(filename, dir_fd=temp_fd)
                except OSError:
                    pass
        if temp_fd is not None:
            os.close(temp_fd)
        if not published and temp_name is not None and viewer_fd is not None:
            try:
                os.rmdir(temp_name, dir_fd=viewer_fd)
            except OSError:
                pass
        for descriptor in (viewer_fd, outputs_fd, root_fd):
            if descriptor is not None:
                os.close(descriptor)

    relative_root = f"outputs/viewer/{capture_id}"
    return CaptureResult(
        capture_id=capture_id,
        screenshot_path=f"{relative_root}/screenshot.png",
        camera_path=f"{relative_root}/camera.json",
        deterministic_camera_digest=digest,
    )
