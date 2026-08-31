"""Local-only standard-library HTTP adapter for Milestone 1A scene APIs."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import hmac
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import re
import socket
import stat
import threading
from typing import Mapping, Sequence
from urllib.parse import parse_qsl, unquote, urlsplit

from .capture import (
    CaptureOutputError,
    InvalidCapture,
    InvalidCapturePNG,
    MAX_CAPTURE_BODY_BYTES,
    MAX_CONCURRENT_CAPTURE_WRITES,
    ViewerSessionStore,
    decode_capture_payload,
    save_reference_capture,
)

from .scene import (
    DecoderInvocationError,
    DecoderUnavailableError,
    MemoryBudgetExceeded,
    OutputExistsError,
    PLYError,
    SourceChangedError,
    UnsafePathError,
    compare_canonical_ply,
    decode_compressed_ply,
    find_repository_root,
    fingerprint_file,
    inspect_compressed_ply,
    load_canonical_ply,
    measure_runtime_memory,
    milestone_output_root,
    validate_canonical_ply,
    write_canonical_ply,
)
from .viewer import (
    InvalidSceneID,
    InvalidViewerSource,
    DEFAULT_SCENE_ID,
    MAX_CONCURRENT_VIEWER_OPERATIONS,
    PinnedViewerSource,
    ViewerSourceConfig,
    ViewerSourceChanged,
    ViewerSourceUnavailable,
    build_viewer_manifest,
    normalize_viewer_sources,
    open_viewer_stream,
    validate_scene_id,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_JSON_BODY_BYTES = 64 * 1024
_ALLOWED_BIND_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_SAFE_OUTPUT_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_HEAVY_OPERATION_LOCK = threading.Lock()
PLAYCANVAS_VERSION = "2.3.3"
PLAYCANVAS_MODULE_ROUTE = f"/vendor/playcanvas-{PLAYCANVAS_VERSION}.mjs"
_PLAYCANVAS_MODULE_RELATIVE = Path("node_modules/playcanvas/build/playcanvas.mjs")
_PLAYCANVAS_PACKAGE_RELATIVE = Path("node_modules/playcanvas/package.json")
_PLAYCANVAS_MODULE_SHA256 = "4b18241d684e3676109100f61aa3ad3488f8f95f632fdbb4433290a315dbc875"
_VENDOR_STREAM_CHUNK_BYTES = 256 * 1024
_CAPTURE_CONTENT_TYPE = re.compile(
    r'^\s*application/json\s*;\s*charset\s*=\s*(?:utf-8|"utf-8")\s*$',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PinnedPlayCanvasModule:
    """One exact, locally installed PlayCanvas browser bundle."""

    path: Path
    identity: tuple[int, int, int, int, int, int]
    size_bytes: int


def _file_identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _pin_playcanvas_module(repository_root: Path) -> PinnedPlayCanvasModule:
    """Verify the exact pinned package and bundle before opening the server."""

    package_path = repository_root / _PLAYCANVAS_PACKAGE_RELATIVE
    module_path = repository_root / _PLAYCANVAS_MODULE_RELATIVE
    try:
        package_info = package_path.lstat()
        if not stat.S_ISREG(package_info.st_mode) or stat.S_ISLNK(package_info.st_mode):
            raise RuntimeError("pinned PlayCanvas package metadata is unavailable")
        if package_info.st_size > 256 * 1024:
            raise RuntimeError("pinned PlayCanvas package metadata is invalid")
        package = json.loads(package_path.read_text(encoding="utf-8"))
        if not isinstance(package, dict) or package.get("version") != PLAYCANVAS_VERSION:
            raise RuntimeError(f"PlayCanvas {PLAYCANVAS_VERSION} is required")

        module_info = module_path.lstat()
        if not stat.S_ISREG(module_info.st_mode) or stat.S_ISLNK(module_info.st_mode):
            raise RuntimeError("pinned PlayCanvas browser module is unavailable")
        digest = hashlib.sha256()
        with module_path.open("rb") as handle:
            for block in iter(lambda: handle.read(_VENDOR_STREAM_CHUNK_BYTES), b""):
                digest.update(block)
        if digest.hexdigest() != _PLAYCANVAS_MODULE_SHA256:
            raise RuntimeError("pinned PlayCanvas browser module failed integrity validation")
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("pinned PlayCanvas 2.3.3 installation is unavailable") from exc
    return PinnedPlayCanvasModule(
        path=module_path,
        identity=_file_identity(module_info),
        size_bytes=module_info.st_size,
    )


class APIError(Exception):
    """A controlled client-facing API error."""

    def __init__(self, status: int, error_type: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type
        self.message = message


def _parse_json_object(raw: bytes) -> dict[str, object]:
    """Decode strict UTF-8 JSON with unique object keys and finite constants."""

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise APIError(
            HTTPStatus.BAD_REQUEST,
            "malformed_json",
            "JSON body must be UTF-8",
        ) from exc

    def reject_duplicate_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result

    def reject_nonstandard_constant(_value: str) -> object:
        raise ValueError("non-standard JSON constant")

    try:
        parsed = json.loads(
            text,
            object_pairs_hook=reject_duplicate_pairs,
            parse_constant=reject_nonstandard_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise APIError(
            HTTPStatus.BAD_REQUEST,
            "malformed_json",
            "request body is not valid JSON",
        ) from exc
    return _require_object(parsed)


def validate_bind_host(host: str) -> str:
    """Return a normalized loopback bind host or reject remote exposure."""

    if not isinstance(host, str):
        raise ValueError("host must be a loopback address")
    normalized = host.strip().lower()
    if normalized not in _ALLOWED_BIND_HOSTS:
        raise ValueError(
            "host must be one of 127.0.0.1, localhost, or ::1; "
            "remote network binding is disabled"
        )
    return normalized


def _is_loopback_client(address: str) -> bool:
    try:
        return ipaddress.ip_address(address.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _is_loopback_host_header(value: str) -> bool:
    """Accept only unambiguous loopback HTTP Host forms."""

    host_value = value.strip().lower()
    if not host_value or any(character in host_value for character in "/\\?#@, "):
        return False
    if host_value.startswith("["):
        closing = host_value.find("]")
        if closing < 0:
            return False
        host = host_value[1:closing]
        remainder = host_value[closing + 1 :]
        if remainder and not remainder.startswith(":"):
            return False
        port = remainder[1:] if remainder else None
    else:
        if host_value.count(":") > 1:
            return False
        host, separator, port_text = host_value.partition(":")
        port = port_text if separator else None
    if host not in _ALLOWED_BIND_HOSTS:
        return False
    if port is None:
        return True
    if len(port) > 5 or not port.isascii() or not port.isdigit():
        return False
    return 1 <= int(port, 10) <= 65535


def _source_file(value: object, *, field_name: str) -> Path:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        raise APIError(
            HTTPStatus.BAD_REQUEST,
            "invalid_request",
            f"{field_name} must be a path string",
        )
    try:
        path = Path(value).expanduser().resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
        raise APIError(
            HTTPStatus.NOT_FOUND,
            "source_not_found",
            f"{field_name} is not an existing regular file",
        ) from exc
    try:
        mode = path.stat().st_mode
    except OSError as exc:
        raise APIError(
            HTTPStatus.NOT_FOUND,
            "source_not_found",
            f"{field_name} is not an existing regular file",
        ) from exc
    if not stat.S_ISREG(mode):
        raise APIError(
            HTTPStatus.NOT_FOUND,
            "source_not_found",
            f"{field_name} is not an existing regular file",
        )
    return path


def _output_name(value: object) -> str:
    """Validate a portable relative PLY name before secure scene APIs recheck it."""

    if not isinstance(value, str) or not value or len(value) > 240:
        raise APIError(
            HTTPStatus.BAD_REQUEST,
            "invalid_output_name",
            "output_name must be a short relative PLY name",
        )
    if "\\" in value or "\x00" in value:
        raise APIError(
            HTTPStatus.BAD_REQUEST,
            "invalid_output_name",
            "output_name contains an unsafe path character",
        )
    path = Path(value)
    unsafe_part = any(part in {"", ".", ".."} for part in path.parts)
    if path.is_absolute() or not path.parts or unsafe_part:
        raise APIError(
            HTTPStatus.BAD_REQUEST,
            "invalid_output_name",
            "output_name must remain relative and cannot traverse directories",
        )
    if any(_SAFE_OUTPUT_COMPONENT.fullmatch(part) is None for part in path.parts):
        raise APIError(
            HTTPStatus.BAD_REQUEST,
            "invalid_output_name",
            "output_name may use only letters, numbers, '.', '_', '-', and safe subdirectories",
        )
    if path.suffix.lower() != ".ply":
        raise APIError(
            HTTPStatus.BAD_REQUEST,
            "invalid_output_name",
            "output_name must end in .ply",
        )
    return path.as_posix()


def _default_output_name(source: Path, operation: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source.stem).strip("._-") or "scene"
    return _output_name(f"{stem[:200]}.{operation}.ply")


def _require_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise APIError(HTTPStatus.BAD_REQUEST, "invalid_request", "JSON body must be an object")
    return value


def _validate_keys(payload: Mapping[str, object], *, required: set[str], optional: set[str]) -> None:
    missing = sorted(required.difference(payload))
    unknown = sorted(set(payload).difference(required | optional))
    if missing:
        raise APIError(HTTPStatus.BAD_REQUEST, "invalid_request", f"missing required field: {missing[0]}")
    if unknown:
        raise APIError(HTTPStatus.BAD_REQUEST, "invalid_request", f"unsupported field: {unknown[0]}")


def _relative_output(path: Path, repository_root: Path) -> str:
    output_root = milestone_output_root(repository_root).resolve(strict=False)
    return path.resolve(strict=False).relative_to(output_root).as_posix()


def _inspect_operation(payload: Mapping[str, object], repository_root: Path) -> dict[str, object]:
    del repository_root
    _validate_keys(payload, required={"source_path"}, optional=set())
    source = _source_file(payload["source_path"], field_name="source_path")
    before = fingerprint_file(source)
    inspection = inspect_compressed_ply(source)
    after = fingerprint_file(source)
    if before != after:
        raise SourceChangedError("source changed during inspection")
    return {
        "format": "playcanvas_compressed_ply",
        "source": {"sha256": before.sha256, "size_bytes": before.size_bytes},
        "inspection": inspection.as_dict(),
    }


def _decode_operation(payload: Mapping[str, object], repository_root: Path) -> dict[str, object]:
    _validate_keys(payload, required={"source_path"}, optional={"output_name"})
    source = _source_file(payload["source_path"], field_name="source_path")
    raw_output = payload.get("output_name")
    output = _output_name(raw_output) if raw_output is not None else _default_output_name(source, "decoded")
    report = decode_compressed_ply(source, output, repository_root=repository_root)
    return {
        "output": {"name": _relative_output(report.output_path, repository_root)},
        "source": {
            "sha256_before": report.source_sha256_before,
            "sha256_after": report.source_sha256_after,
        },
        "gaussian_count": report.gaussian_count,
        "runtime": {
            "elapsed_seconds": report.runtime.elapsed_seconds,
            "peak_memory_bytes": report.runtime.peak_memory_bytes,
            "memory_source": report.runtime.memory_source,
        },
    }


def _canonical_beneath_output(value: object, repository_root: Path) -> Path:
    source = _source_file(value, field_name="canonical_path")
    lexical_root = milestone_output_root(repository_root).absolute()
    output_root = lexical_root.resolve(strict=False)
    if output_root != lexical_root:
        raise APIError(
            HTTPStatus.BAD_REQUEST,
            "unsafe_canonical_path",
            "canonical input is unavailable because the output root is redirected",
        )
    try:
        source.relative_to(output_root)
    except ValueError as exc:
        raise APIError(
            HTTPStatus.BAD_REQUEST,
            "unsafe_canonical_path",
            "canonical_path must resolve beneath outputs/milestone1",
        ) from exc
    return source


def _roundtrip_operation(payload: Mapping[str, object], repository_root: Path) -> dict[str, object]:
    _validate_keys(payload, required={"canonical_path"}, optional={"output_name"})
    source = _canonical_beneath_output(payload["canonical_path"], repository_root)
    raw_output = payload.get("output_name")
    output = _output_name(raw_output) if raw_output is not None else _default_output_name(source, "roundtrip")
    with measure_runtime_memory() as runtime:
        before = fingerprint_file(source)
        inspection = validate_canonical_ply(source, reject_nonfinite=True)
        scene = load_canonical_ply(source, reject_nonfinite=True)
        written = write_canonical_ply(scene, output, repository_root=repository_root)
        comparison = compare_canonical_ply(source, written, exact=True)
        after = fingerprint_file(source)
        if before != after:
            raise SourceChangedError("source changed during canonical round trip")
    runtime["memory_source"] = "process_lifetime_high_water"
    return {
        "output": {"name": _relative_output(written, repository_root)},
        "source": {
            "sha256_before": before.sha256,
            "sha256_after": after.sha256,
            "size_bytes": before.size_bytes,
        },
        "inspection": {
            "gaussian_count": inspection.gaussian_count,
            "property_count": len(inspection.property_names),
            "unknown_property_count": len(inspection.unknown_property_names),
        },
        "comparison": comparison.as_dict(),
        "runtime": runtime,
    }


def _output_listing(repository_root: Path) -> list[dict[str, object]]:
    root = milestone_output_root(repository_root)
    if not root.exists() or root.is_symlink() or not root.is_dir():
        return []
    outputs: list[dict[str, object]] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_names[:] = sorted(
            name
            for name in directory_names
            if not name.startswith(".") and not (Path(directory) / name).is_symlink()
        )
        for name in sorted(file_names):
            if name.startswith("."):
                continue
            path = Path(directory) / name
            try:
                info = path.lstat()
            except OSError:
                continue
            relative_name = path.relative_to(root).as_posix()
            try:
                _output_name(relative_name)
            except APIError:
                continue
            if stat.S_ISREG(info.st_mode):
                outputs.append({"name": relative_name, "size_bytes": info.st_size})
    outputs.sort(key=lambda item: str(item["name"]))
    return outputs


class SceneAgentRequestHandler(BaseHTTPRequestHandler):
    """Small explicit HTTP surface with JSON errors and no CORS support."""

    server_version = "SceneAgentLocal/0.1"
    sys_version = ""

    @property
    def repository_root(self) -> Path:
        return self.server.repository_root  # type: ignore[attr-defined]

    @property
    def web_root(self) -> Path:
        return self.server.web_root  # type: ignore[attr-defined]

    @property
    def viewer_sources(self) -> Mapping[str, PinnedViewerSource]:
        return self.server.viewer_sources  # type: ignore[attr-defined]

    @property
    def viewer_operation_slots(self) -> threading.BoundedSemaphore:
        return self.server.viewer_operation_slots  # type: ignore[attr-defined]

    @property
    def playcanvas_module(self) -> PinnedPlayCanvasModule:
        return self.server.playcanvas_module  # type: ignore[attr-defined]

    @property
    def viewer_session_store(self) -> ViewerSessionStore:
        return self.server.viewer_session_store  # type: ignore[attr-defined]

    @property
    def capture_write_slots(self) -> threading.BoundedSemaphore:
        return self.server.capture_write_slots  # type: ignore[attr-defined]

    @property
    def capture_scene_id(self) -> str | None:
        return self.server.capture_scene_id  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: object) -> None:
        # Keep the launcher quiet by default and avoid logging user-supplied paths.
        del format, args

    def _headers(self, content_type: str, length: int) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def _send_json(self, status: int, payload: Mapping[str, object]) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self._headers("application/json; charset=utf-8", len(body))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_api_error(self, error: APIError) -> None:
        self._send_json(
            error.status,
            {"ok": False, "error": {"type": error.error_type, "message": error.message}},
        )

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        del message, explain
        self.close_connection = True
        default_message = HTTPStatus._value2member_map_.get(code, HTTPStatus.INTERNAL_SERVER_ERROR).phrase
        self._send_api_error(APIError(code, "http_error", default_message))

    def parse_request(self) -> bool:
        """Apply origin validation once to every successfully parsed request."""

        if not super().parse_request():
            return False
        return self._require_loopback()

    def _path(self) -> str | None:
        try:
            return urlsplit(self.path).path
        except ValueError:
            self._send_api_error(
                APIError(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_request_target",
                    "request target is malformed",
                )
            )
            return None

    def _require_loopback(self) -> bool:
        if not _is_loopback_client(str(self.client_address[0])):
            self.close_connection = True
            self._send_api_error(
                APIError(
                    HTTPStatus.FORBIDDEN,
                    "remote_client_rejected",
                    "only loopback clients are permitted",
                )
            )
            return False
        host_headers = self.headers.get_all("Host", failobj=[])
        if len(host_headers) != 1 or not _is_loopback_host_header(host_headers[0]):
            self.close_connection = True
            self._send_api_error(
                APIError(
                    HTTPStatus.BAD_REQUEST,
                    "invalid_host",
                    "Host must identify an explicit loopback address",
                )
            )
            return False
        return True

    def _method_not_allowed(self, allowed: str) -> None:
        body = json.dumps(
            {
                "ok": False,
                "error": {
                    "type": "method_not_allowed",
                    "message": f"use {allowed} for this endpoint",
                },
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(HTTPStatus.METHOD_NOT_ALLOWED)
        self.send_header("Allow", allowed)
        self._headers("application/json; charset=utf-8", len(body))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _viewer_error(self, error: Exception) -> None:
        if isinstance(error, InvalidSceneID):
            api_error = APIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_scene_id",
                "scene identifier is invalid",
            )
        elif isinstance(error, ViewerSourceUnavailable):
            api_error = APIError(
                HTTPStatus.NOT_FOUND,
                "scene_unavailable",
                "viewer scene is unavailable",
            )
        elif isinstance(error, InvalidViewerSource):
            api_error = APIError(
                HTTPStatus.UNPROCESSABLE_ENTITY,
                "invalid_viewer_source",
                "viewer source is not a valid supported compressed PLY",
            )
        elif isinstance(error, ViewerSourceChanged):
            api_error = APIError(
                HTTPStatus.CONFLICT,
                "source_changed",
                "viewer source changed during the operation",
            )
        else:
            api_error = APIError(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "operation_failed",
                "the local viewer operation could not be completed",
            )
        self._send_api_error(api_error)

    def _manifest_scene_id(self) -> str:
        try:
            target = urlsplit(self.path)
            if target.fragment or len(target.query) > 512:
                raise ValueError("invalid viewer query")
            fields = parse_qsl(
                target.query,
                keep_blank_values=True,
                strict_parsing=True,
                encoding="utf-8",
                errors="strict",
                max_num_fields=2,
            )
        except (UnicodeError, ValueError) as exc:
            raise InvalidSceneID("scene identifier is invalid") from exc
        if len(fields) != 1 or fields[0][0] != "scene_id":
            raise InvalidSceneID("scene identifier is invalid")
        return validate_scene_id(fields[0][1])

    def _source_scene_id(self) -> str:
        prefix = "/api/viewer/source/"
        try:
            target = urlsplit(self.path)
            if target.query or target.fragment or not target.path.startswith(prefix):
                raise ValueError("invalid viewer source target")
            encoded_id = target.path[len(prefix) :]
            if len(encoded_id) > 256:
                raise ValueError("viewer source identifier is too long")
            scene_id = unquote(encoded_id, encoding="utf-8", errors="strict")
        except (UnicodeError, ValueError) as exc:
            raise InvalidSceneID("scene identifier is invalid") from exc
        return validate_scene_id(scene_id)

    def _active_viewer_origin(self) -> str:
        host_headers = self.headers.get_all("Host", failobj=[])
        if len(host_headers) != 1 or not _is_loopback_host_header(host_headers[0]):
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_host",
                "Host must identify an explicit loopback address",
            )
        return f"http://{host_headers[0].strip().lower()}"

    def _serve_viewer_session(self) -> None:
        try:
            target = urlsplit(self.path)
        except ValueError:
            target = None
        if (
            target is None
            or target.path != "/api/viewer/session"
            or target.query
            or target.fragment
        ):
            self._send_api_error(
                APIError(HTTPStatus.NOT_FOUND, "not_found", "viewer session route was not found")
            )
            return
        origin = self._active_viewer_origin()
        client = str(self.client_address[0])
        token, expires_in_seconds = self.viewer_session_store.issue(
            origin=origin,
            client=client,
        )
        self._send_json(
            HTTPStatus.OK,
            {
                "ok": True,
                "viewer_session": {
                    "csrf_token": token,
                    "expires_in_seconds": expires_in_seconds,
                },
            },
        )

    def _authorize_capture_request(self) -> None:
        try:
            target = urlsplit(self.path)
        except ValueError as exc:
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_request_target",
                "request target is malformed",
            ) from exc
        if target.path != "/api/viewer/captures" or target.query or target.fragment:
            raise APIError(
                HTTPStatus.NOT_FOUND,
                "not_found",
                "capture route was not found",
            )
        origin_headers = self.headers.get_all("Origin", failobj=[])
        if len(origin_headers) != 1:
            raise APIError(
                HTTPStatus.FORBIDDEN,
                "invalid_origin",
                "capture requires exactly one active viewer Origin",
            )
        origin = origin_headers[0]
        expected_origin = self._active_viewer_origin()
        if (
            not isinstance(origin, str)
            or len(origin) > 512
            or origin == "null"
            or not hmac.compare_digest(origin, expected_origin)
        ):
            raise APIError(
                HTTPStatus.FORBIDDEN,
                "invalid_origin",
                "capture Origin does not match the active viewer",
            )
        token_headers = self.headers.get_all("X-Viewer-CSRF", failobj=[])
        if len(token_headers) != 1 or not self.viewer_session_store.validate(
            token_headers[0],
            origin=origin,
            client=str(self.client_address[0]),
        ):
            raise APIError(
                HTTPStatus.FORBIDDEN,
                "invalid_csrf",
                "capture session token is absent, invalid, or expired",
            )

    def _serve_viewer_manifest(self) -> None:
        if not self.viewer_operation_slots.acquire(blocking=False):
            self._send_api_error(
                APIError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "viewer_busy",
                    "another viewer operation is already running",
                )
            )
            return
        try:
            try:
                scene_id = self._manifest_scene_id()
                manifest = build_viewer_manifest(self.viewer_sources, scene_id)
            except Exception as exc:
                self._viewer_error(exc)
                return
        finally:
            self.viewer_operation_slots.release()
        self._send_json(HTTPStatus.OK, {"ok": True, **manifest.as_dict()})

    def _serve_viewer_source(self) -> None:
        if not self.viewer_operation_slots.acquire(blocking=False):
            self._send_api_error(
                APIError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "viewer_busy",
                    "another viewer operation is already running",
                )
            )
            return
        headers_sent = False
        try:
            scene_id = self._source_scene_id()
            with open_viewer_stream(self.viewer_sources, scene_id) as (source, fingerprint):
                self.send_response(HTTPStatus.OK)
                self._headers("application/octet-stream", fingerprint.size_bytes)
                self.send_header("Digest", fingerprint.digest_header)
                self.send_header("X-Scene-SHA256", fingerprint.sha256)
                self.end_headers()
                headers_sent = True
                source.stream_to(self.wfile, fingerprint)
        except (BrokenPipeError, ConnectionError):
            self.close_connection = True
        except Exception as exc:
            if headers_sent:
                self.close_connection = True
            else:
                self._viewer_error(exc)
        finally:
            self.viewer_operation_slots.release()

    def _read_declared_body(self, maximum_bytes: int, too_large_message: str) -> bytes:
        transfer_encodings = self.headers.get_all("Transfer-Encoding", failobj=[])
        if transfer_encodings:
            self.close_connection = True
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_request",
                "streamed request bodies are not supported",
            )
        raw_lengths = self.headers.get_all("Content-Length", failobj=[])
        if not raw_lengths:
            self.close_connection = True
            raise APIError(
                HTTPStatus.LENGTH_REQUIRED,
                "length_required",
                "Content-Length is required",
            )
        if len(raw_lengths) != 1:
            self.close_connection = True
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_request",
                "exactly one Content-Length header is permitted",
            )
        raw_length = raw_lengths[0]
        if (
            len(raw_length) > 20
            or re.fullmatch(r"(?:0|[1-9][0-9]*)", raw_length) is None
        ):
            self.close_connection = True
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "invalid_request",
                "Content-Length must be one canonical ASCII-decimal integer",
            )
        length = int(raw_length, 10)
        if length > maximum_bytes:
            self.close_connection = True
            raise APIError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "body_too_large",
                too_large_message,
            )
        raw = self.rfile.read(length)
        if len(raw) != length:
            self.close_connection = True
            raise APIError(
                HTTPStatus.BAD_REQUEST,
                "truncated_body",
                "request body ended before Content-Length bytes were received",
            )
        return raw

    def _read_json(self) -> dict[str, object]:
        content_type = self.headers.get("Content-Type", "")
        media_type = content_type.split(";", 1)[0].strip().lower()
        if media_type != "application/json":
            raise APIError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "unsupported_media_type",
                "POST requests require application/json",
            )
        raw = self._read_declared_body(
            MAX_JSON_BODY_BYTES,
            f"JSON body exceeds {MAX_JSON_BODY_BYTES} bytes",
        )
        return _parse_json_object(raw)

    def _read_capture_json(self) -> dict[str, object]:
        content_types = self.headers.get_all("Content-Type", failobj=[])
        if len(content_types) != 1 or _CAPTURE_CONTENT_TYPE.fullmatch(content_types[0]) is None:
            raise APIError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "unsupported_media_type",
                "capture requires application/json; charset=utf-8",
            )
        raw = self._read_declared_body(
            MAX_CAPTURE_BODY_BYTES,
            f"capture body exceeds {MAX_CAPTURE_BODY_BYTES} bytes",
        )
        return _parse_json_object(raw)

    def _dispatch_operation(self, operation, payload: Mapping[str, object]) -> None:
        try:
            result = operation(payload, self.repository_root)
        except APIError as exc:
            self._send_api_error(exc)
        except FileNotFoundError:
            self._send_api_error(
                APIError(
                    HTTPStatus.NOT_FOUND,
                    "source_not_found",
                    "source file was not found",
                )
            )
        except UnsafePathError:
            self._send_api_error(
                APIError(
                    HTTPStatus.BAD_REQUEST,
                    "unsafe_path",
                    "the requested output path is not permitted",
                )
            )
        except OutputExistsError:
            self._send_api_error(
                APIError(
                    HTTPStatus.CONFLICT,
                    "output_exists",
                    "the output already exists and will not be overwritten",
                )
            )
        except SourceChangedError:
            self._send_api_error(
                APIError(
                    HTTPStatus.CONFLICT,
                    "source_changed",
                    "the source changed during the operation",
                )
            )
        except DecoderUnavailableError:
            self._send_api_error(
                APIError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "decoder_unavailable",
                    "the local compressed-PLY decoder is unavailable",
                )
            )
        except MemoryBudgetExceeded:
            self._send_api_error(
                APIError(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "memory_budget_exceeded",
                    "the operation exceeds the local memory limit",
                )
            )
        except DecoderInvocationError:
            self._send_api_error(
                APIError(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "decoder_failed",
                    "the decoder did not produce a valid canonical scene",
                )
            )
        except PLYError:
            self._send_api_error(
                APIError(
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                    "invalid_ply",
                    "source is not a valid supported PLY file",
                )
            )
        except Exception:
            self._send_api_error(
                APIError(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "operation_failed",
                    "the local operation could not be completed",
                )
            )
        else:
            self._send_json(HTTPStatus.OK, {"ok": True, **result})

    def _serve_static(self, requested_path: str) -> None:
        relative = "index.html" if requested_path == "/" else unquote(requested_path[len("/static/") :])
        if not relative or "\x00" in relative:
            self._send_api_error(APIError(HTTPStatus.NOT_FOUND, "not_found", "static asset was not found"))
            return
        root = self.web_root.resolve(strict=False)
        try:
            candidate = (root / relative).resolve(strict=True)
            candidate.relative_to(root)
        except (FileNotFoundError, OSError, RuntimeError, ValueError):
            self._send_api_error(APIError(HTTPStatus.NOT_FOUND, "not_found", "static asset was not found"))
            return
        if not candidate.is_file():
            self._send_api_error(APIError(HTTPStatus.NOT_FOUND, "not_found", "static asset was not found"))
            return
        try:
            body = candidate.read_bytes()
        except OSError:
            self._send_api_error(APIError(HTTPStatus.NOT_FOUND, "not_found", "static asset was not found"))
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self._headers(content_type, len(body))
        if candidate.name == "index.html":
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'; "
                "connect-src 'self'; img-src 'self' data:; object-src 'none'; script-src 'self'; "
                "style-src 'self'; worker-src blob:",
            )
        self.end_headers()
        self.wfile.write(body)

    def _serve_playcanvas_module(self) -> None:
        """Stream only the exact verified browser bundle, never node_modules generally."""

        try:
            target = urlsplit(self.path)
        except ValueError:
            target = None
        if (
            target is None
            or target.path != PLAYCANVAS_MODULE_ROUTE
            or target.query
            or target.fragment
        ):
            self._send_api_error(
                APIError(HTTPStatus.NOT_FOUND, "not_found", "vendor module was not found")
            )
            return
        pinned = self.playcanvas_module
        try:
            path_info = pinned.path.lstat()
            if (
                not stat.S_ISREG(path_info.st_mode)
                or stat.S_ISLNK(path_info.st_mode)
                or _file_identity(path_info) != pinned.identity
            ):
                raise OSError("module identity changed")
            handle = pinned.path.open("rb")
            descriptor_info = os.fstat(handle.fileno())
            if _file_identity(descriptor_info) != pinned.identity:
                handle.close()
                raise OSError("module identity changed")
        except OSError:
            self._send_api_error(
                APIError(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "renderer_unavailable",
                    f"the pinned PlayCanvas {PLAYCANVAS_VERSION} module is unavailable",
                )
            )
            return

        with handle:
            self.send_response(HTTPStatus.OK)
            self._headers("application/javascript; charset=utf-8", pinned.size_bytes)
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.end_headers()
            try:
                while True:
                    block = handle.read(_VENDOR_STREAM_CHUNK_BYTES)
                    if not block:
                        break
                    self.wfile.write(block)
            except (BrokenPipeError, ConnectionError):
                self.close_connection = True

    def do_GET(self) -> None:
        path = self._path()
        if path is None:
            return
        if path == "/api/viewer/manifest":
            self._serve_viewer_manifest()
        elif path == "/api/viewer/source" or path.startswith("/api/viewer/source/"):
            self._serve_viewer_source()
        elif path == PLAYCANVAS_MODULE_ROUTE:
            self._serve_playcanvas_module()
        elif path == "/api/status":
            self._send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "service": "scene-agent-ui",
                    "local_only": True,
                    "compute": "cpu",
                    "busy": _HEAVY_OPERATION_LOCK.locked(),
                    "operations": ("inspect", "decode", "roundtrip"),
                },
            )
        elif path == "/api/outputs":
            outputs = _output_listing(self.repository_root)
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "root": "outputs/milestone1", "count": len(outputs), "outputs": outputs},
            )
        elif path == "/" or path.startswith("/static/"):
            self._serve_static(path)
        elif path in {"/api/inspect", "/api/decode", "/api/roundtrip"}:
            self._method_not_allowed("POST")
        else:
            self._send_api_error(APIError(HTTPStatus.NOT_FOUND, "not_found", "route was not found"))

    def do_POST(self) -> None:
        path = self._path()
        if path is None:
            return
        if path in {"/api/viewer/manifest", "/api/viewer/source"} or path.startswith(
            "/api/viewer/source/"
        ):
            self._method_not_allowed("GET")
            return
        if (
            path in {"/api/status", "/api/outputs", PLAYCANVAS_MODULE_ROUTE}
            or path == "/"
            or path.startswith("/static/")
        ):
            self._method_not_allowed("GET")
            return
        operations = {
            "/api/inspect": (_inspect_operation, False),
            "/api/decode": (_decode_operation, True),
            "/api/roundtrip": (_roundtrip_operation, True),
        }
        selected = operations.get(path)
        if selected is None:
            self._send_api_error(APIError(HTTPStatus.NOT_FOUND, "not_found", "route was not found"))
            return
        try:
            payload = self._read_json()
        except APIError as exc:
            self._send_api_error(exc)
            return
        operation, heavy = selected
        if not heavy:
            self._dispatch_operation(operation, payload)
            return
        if not _HEAVY_OPERATION_LOCK.acquire(blocking=False):
            self._send_api_error(
                APIError(
                    HTTPStatus.CONFLICT,
                    "busy",
                    "another decode or round-trip operation is already running",
                )
            )
            return
        try:
            self._dispatch_operation(operation, payload)
        finally:
            _HEAVY_OPERATION_LOCK.release()

    def do_HEAD(self) -> None:
        path = self._path()
        if path == PLAYCANVAS_MODULE_ROUTE:
            self._method_not_allowed("GET")
        elif path == "/api/viewer/manifest" or (
            path is not None
            and (path == "/api/viewer/source" or path.startswith("/api/viewer/source/"))
        ):
            self._method_not_allowed("GET")
        elif path is not None:
            self._method_not_allowed("GET, POST")

    def do_OPTIONS(self) -> None:
        path = self._path()
        if path == PLAYCANVAS_MODULE_ROUTE:
            self._method_not_allowed("GET")
        elif path == "/api/viewer/manifest" or (
            path is not None
            and (path == "/api/viewer/source" or path.startswith("/api/viewer/source/"))
        ):
            self._method_not_allowed("GET")
        elif path is not None:
            self._method_not_allowed("GET, POST")

    do_PUT = do_OPTIONS
    do_PATCH = do_OPTIONS
    do_DELETE = do_OPTIONS
    do_TRACE = do_OPTIONS


class LocalThreadingHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class IPv6LocalThreadingHTTPServer(LocalThreadingHTTPServer):
    address_family = socket.AF_INET6


def create_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    repository_root: str | Path | None = None,
    web_root: str | Path | None = None,
    viewer_sources: Mapping[str, ViewerSourceConfig] | None = None,
) -> LocalThreadingHTTPServer:
    """Create, but do not start, a loopback-only threaded HTTP server."""

    normalized_host = validate_bind_host(host)
    resolved_repository_root = find_repository_root(repository_root).resolve()
    playcanvas_module = _pin_playcanvas_module(resolved_repository_root)
    configured_viewer_sources = normalize_viewer_sources(viewer_sources)
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise ValueError("port must be an integer between 0 and 65535")
    server_type = (
        IPv6LocalThreadingHTTPServer
        if normalized_host == "::1"
        else LocalThreadingHTTPServer
    )
    server = server_type((normalized_host, port), SceneAgentRequestHandler)
    server.repository_root = resolved_repository_root  # type: ignore[attr-defined]
    configured_web_root = (
        Path(web_root) if web_root is not None else Path(__file__).with_name("web")
    )
    server.web_root = configured_web_root.resolve(strict=False)  # type: ignore[attr-defined]
    server.viewer_sources = configured_viewer_sources  # type: ignore[attr-defined]
    server.playcanvas_module = playcanvas_module  # type: ignore[attr-defined]
    server.viewer_operation_slots = threading.BoundedSemaphore(  # type: ignore[attr-defined]
        MAX_CONCURRENT_VIEWER_OPERATIONS
    )
    return server


def _cli_port(value: str) -> int:
    try:
        port = int(value, 10)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scene-agent-ui",
        description="Run the local-only Scene Agent Milestone 1A interface.",
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help="loopback host: 127.0.0.1, localhost, or ::1",
    )
    parser.add_argument(
        "--port",
        default=DEFAULT_PORT,
        type=_cli_port,
        help=f"local TCP port (default: {DEFAULT_PORT})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        host = validate_bind_host(arguments.host)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        server = create_server(host, arguments.port)
    except OSError as exc:
        parser.error(f"could not bind local server: {exc}")
    display_host = f"[{host}]" if ":" in host else host
    print(f"Scene Agent UI listening locally at http://{display_host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through package launcher
    raise SystemExit(main())
