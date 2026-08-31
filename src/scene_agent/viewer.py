"""Allowlisted, immutable compressed-scene contracts for the local viewer."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import base64
import hashlib
import math
import os
from pathlib import Path
import re
import stat
import struct
from types import MappingProxyType
from typing import BinaryIO, Iterator, Mapping

from .scene.compressed import (
    CHUNK_PROPERTIES,
    CHUNK_SIZE,
    PACKED_VERTEX_PROPERTIES,
    SH_PROPERTIES,
)
from .scene.errors import PLYError
from .scene.ply import MAX_HEADER_BYTES, PLYHeader, parse_ply_header


VIEWER_FORMAT = "playcanvas_compressed_ply"
STREAM_CHUNK_BYTES = 1024 * 1024
MAX_CONCURRENT_VIEWER_OPERATIONS = 1
_HEADER_CHUNK_BYTES = 64 * 1024
_FLOAT32_MAX = 3.4028234663852886e38
_SH_C0 = 0.28209479177387814
_SCENE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_HEADER_END = re.compile(rb"(?m)^end_header\r?\n")


class ViewerError(Exception):
    """Base class for controlled viewer-source failures."""


class InvalidSceneID(ViewerError):
    """The client supplied a syntactically unsafe logical identifier."""


class ViewerSourceUnavailable(ViewerError):
    """The logical scene is unknown or its configured source is unavailable."""


class InvalidViewerSource(ViewerError):
    """An allowlisted source does not match the supported compressed schema."""


class ViewerSourceChanged(ViewerError):
    """An allowlisted source changed during a read-only operation."""


@dataclass(frozen=True)
class ViewerCoordinateSystem:
    """Trusted coordinate provenance supplied by server configuration."""

    world_up: str
    floor_axes: tuple[str, str]
    units: str

    def as_dict(self) -> dict[str, object]:
        return {
            "world_up": self.world_up,
            "floor_axes": list(self.floor_axes),
            "units": self.units,
        }


Z_UP_COORDINATE_SYSTEM = ViewerCoordinateSystem(
    world_up="+Z",
    floor_axes=("+X", "+Y"),
    units="scene_units",
)


@dataclass(frozen=True)
class ViewerSourceConfig:
    """One trusted allowlist entry before its path is canonicalized and pinned."""

    path: str | os.PathLike[str]
    coordinate_system: ViewerCoordinateSystem


@dataclass(frozen=True)
class PinnedViewerSource:
    """Canonical target and identity fixed at server-configuration time."""

    canonical_path: Path | None
    pinned_identity: tuple[int, int, int, int, int, int] | None
    coordinate_system: ViewerCoordinateSystem


DEFAULT_SCENE_ID = "interiorgs_0231_840445"
DEFAULT_VIEWER_SOURCES: Mapping[str, ViewerSourceConfig] = MappingProxyType(
    {
        DEFAULT_SCENE_ID: ViewerSourceConfig(
            path=(
                "/data/Desktop/Prashansa/major/scenebench/data/InteriorGS/"
                "0231_840445/3dgs_compressed.ply"
            ),
            coordinate_system=Z_UP_COORDINATE_SYSTEM,
        )
    }
)


@dataclass(frozen=True)
class ViewerFingerprint:
    sha256: str
    size_bytes: int

    @property
    def digest_header(self) -> str:
        encoded = base64.b64encode(bytes.fromhex(self.sha256)).decode("ascii")
        return f"sha-256={encoded}"


@dataclass(frozen=True)
class ViewerInspection:
    gaussian_count: int
    chunk_count: int
    payload_offset: int
    scene_aabb_min: tuple[float, float, float]
    scene_aabb_max: tuple[float, float, float]


@dataclass(frozen=True)
class ViewerManifest:
    scene_id: str
    fingerprint: ViewerFingerprint
    inspection: ViewerInspection
    coordinate_system: ViewerCoordinateSystem

    def as_dict(self) -> dict[str, object]:
        return {
            "scene_id": self.scene_id,
            "format": VIEWER_FORMAT,
            "sha256": self.fingerprint.sha256,
            "size_bytes": self.fingerprint.size_bytes,
            "gaussian_count": self.inspection.gaussian_count,
            "chunk_count": self.inspection.chunk_count,
            "coordinate_system": self.coordinate_system.as_dict(),
            "scene_aabb": {
                "min": list(self.inspection.scene_aabb_min),
                "max": list(self.inspection.scene_aabb_max),
            },
        }


def validate_scene_id(scene_id: object) -> str:
    """Return one conservative logical scene ID, never a filesystem path."""

    if not isinstance(scene_id, str) or _SCENE_ID.fullmatch(scene_id) is None:
        raise InvalidSceneID("scene identifier is invalid")
    return scene_id


def normalize_viewer_sources(
    sources: Mapping[str, ViewerSourceConfig] | None,
) -> Mapping[str, PinnedViewerSource]:
    """Canonicalize trusted aliases and pin source identity for this server."""

    configured = DEFAULT_VIEWER_SOURCES if sources is None else sources
    if not isinstance(configured, Mapping):
        raise ValueError("viewer_sources must be a mapping of scene IDs to source configs")
    normalized: dict[str, PinnedViewerSource] = {}
    for raw_id, config in configured.items():
        try:
            scene_id = validate_scene_id(raw_id)
        except InvalidSceneID as exc:
            raise ValueError("viewer_sources contains an invalid scene ID") from exc
        if not isinstance(config, ViewerSourceConfig):
            raise ValueError(
                "viewer_sources values must be ViewerSourceConfig instances "
                "with trusted coordinates"
            )
        if config.coordinate_system != Z_UP_COORDINATE_SYSTEM:
            raise ValueError("M1 viewer sources require explicit trusted +Z coordinates")
        if not isinstance(config.path, (str, os.PathLike)):
            raise ValueError("viewer source config path must be a filesystem path")
        try:
            lexical_path = Path(config.path).expanduser().absolute()
            lexical_info = os.lstat(lexical_path)
            if stat.S_ISLNK(lexical_info.st_mode) or not stat.S_ISREG(
                lexical_info.st_mode
            ):
                raise ValueError("viewer source must be a non-symlink regular file")
            canonical_path = lexical_path.resolve(strict=True)
            if canonical_path.suffix.lower() != ".ply":
                raise ValueError("viewer source must use the .ply extension")
            fd = _open_canonical_path(canonical_path)
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or _identity(info) != _identity(lexical_info)
                ):
                    raise ValueError(
                        "viewer source identity changed during configuration"
                    )
                pinned_identity = _identity(info)
            finally:
                os.close(fd)
        except (OSError, RuntimeError, ValueError) as exc:
            if sources is None:
                normalized[scene_id] = PinnedViewerSource(
                    canonical_path=None,
                    pinned_identity=None,
                    coordinate_system=config.coordinate_system,
                )
                continue
            raise ValueError("viewer_sources contains an unavailable or unsafe source") from exc
        normalized[scene_id] = PinnedViewerSource(
            canonical_path=canonical_path,
            pinned_identity=pinned_identity,
            coordinate_system=config.coordinate_system,
        )
    return MappingProxyType(normalized)


def _identity(info: os.stat_result) -> tuple[int, int, int, int, int, int]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _open_canonical_path(path: Path) -> int:
    """Open an absolute canonical file by no-follow descriptor traversal."""

    if not path.is_absolute() or len(path.parts) < 2:
        raise ValueError("viewer source canonical path must be absolute")
    if not hasattr(os, "O_NOFOLLOW") or os.open not in os.supports_dir_fd:
        raise RuntimeError("secure descriptor-relative traversal is unavailable")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    directory_fd = os.open(path.anchor, directory_flags)
    try:
        for component in path.parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = next_fd
        return os.open(path.name, file_flags, dir_fd=directory_fd)
    finally:
        os.close(directory_fd)


def _read_exact_at(fd: int, size: int, offset: int) -> bytes:
    if size <= 0 or size > STREAM_CHUNK_BYTES:
        raise ValueError("descriptor reads must be positive and at most 1 MiB")
    chunks = bytearray()
    while len(chunks) < size:
        block = os.pread(fd, size - len(chunks), offset + len(chunks))
        if not block:
            raise ViewerSourceChanged("source ended during a bounded read")
        chunks.extend(block)
    return bytes(chunks)


def _read_header(fd: int, size_bytes: int) -> PLYHeader:
    observed = bytearray()
    limit = min(size_bytes, MAX_HEADER_BYTES + 1)
    offset = 0
    while offset < limit:
        requested = min(_HEADER_CHUNK_BYTES, limit - offset)
        observed.extend(_read_exact_at(fd, requested, offset))
        match = _HEADER_END.search(observed)
        if match is not None:
            try:
                return parse_ply_header(bytes(observed[: match.end()]))
            except (PLYError, UnicodeError, ValueError) as exc:
                raise InvalidViewerSource("unsupported compressed PLY header") from exc
        offset += requested
    raise InvalidViewerSource("unsupported or oversized compressed PLY header")


def _expect_element(
    element: object,
    *,
    name: str,
    count: int | None,
    properties: tuple[str, ...],
    type_name: str,
) -> None:
    actual_name = getattr(element, "name", None)
    actual_count = getattr(element, "count", None)
    actual_properties = getattr(element, "properties", ())
    if actual_name != name or (count is not None and actual_count != count):
        raise InvalidViewerSource("unsupported compressed PLY element layout")
    if tuple(getattr(prop, "name", None) for prop in actual_properties) != properties:
        raise InvalidViewerSource("unsupported compressed PLY property layout")
    if any(
        getattr(prop, "is_list", True)
        or getattr(prop, "type_name", None) != type_name
        for prop in actual_properties
    ):
        raise InvalidViewerSource("unsupported compressed PLY property types")


class OpenViewerSource:
    """One stable read-only descriptor for a configured viewer source."""

    def __init__(
        self,
        scene_id: str,
        config: PinnedViewerSource,
        fd: int,
        opened_identity: tuple[int, int, int, int, int, int],
    ) -> None:
        self.scene_id = scene_id
        self.config = config
        self._fd = fd
        self._opened_identity = opened_identity

    @property
    def size_bytes(self) -> int:
        return self._opened_identity[3]

    def _assert_stable(self) -> None:
        try:
            descriptor_info = os.fstat(self._fd)
            assert self.config.canonical_path is not None
            path_fd = _open_canonical_path(self.config.canonical_path)
            try:
                path_info = os.fstat(path_fd)
            finally:
                os.close(path_fd)
        except OSError as exc:
            raise ViewerSourceChanged("source identity changed") from exc
        if (
            _identity(descriptor_info) != self._opened_identity
            or _identity(path_info) != self._opened_identity
            or self.config.pinned_identity != self._opened_identity
        ):
            raise ViewerSourceChanged("source identity changed")

    def fingerprint(self) -> ViewerFingerprint:
        digest = hashlib.sha256()
        offset = 0
        while offset < self.size_bytes:
            requested = min(STREAM_CHUNK_BYTES, self.size_bytes - offset)
            block = _read_exact_at(self._fd, requested, offset)
            digest.update(block)
            offset += len(block)
        if os.pread(self._fd, 1, self.size_bytes):
            raise ViewerSourceChanged("source grew during fingerprinting")
        self._assert_stable()
        return ViewerFingerprint(digest.hexdigest(), self.size_bytes)

    def inspect(self) -> ViewerInspection:
        header = _read_header(self._fd, self.size_bytes)
        if header.format_name != "binary_little_endian" or header.version != "1.0":
            raise InvalidViewerSource("unsupported compressed PLY format")
        if len(header.elements) != 3:
            raise InvalidViewerSource("unsupported compressed PLY element layout")
        chunk, vertex, sh = header.elements
        if chunk.count <= 0 or vertex.count <= 0 or sh.count <= 0:
            raise InvalidViewerSource("compressed PLY element counts must be positive")
        expected_chunks = (vertex.count + CHUNK_SIZE - 1) // CHUNK_SIZE
        _expect_element(
            chunk,
            name="chunk",
            count=expected_chunks,
            properties=CHUNK_PROPERTIES,
            type_name="float",
        )
        _expect_element(
            vertex,
            name="vertex",
            count=None,
            properties=PACKED_VERTEX_PROPERTIES,
            type_name="uint",
        )
        _expect_element(
            sh,
            name="sh",
            count=vertex.count,
            properties=SH_PROPERTIES,
            type_name="uchar",
        )
        expected_payload = (
            chunk.count * len(CHUNK_PROPERTIES) * 4
            + vertex.count * len(PACKED_VERTEX_PROPERTIES) * 4
            + sh.count * len(SH_PROPERTIES)
        )
        if self.size_bytes - header.payload_offset != expected_payload:
            raise InvalidViewerSource("compressed PLY payload size is invalid")

        record_size = len(CHUNK_PROPERTIES) * 4
        records_per_read = max(1, STREAM_CHUNK_BYTES // record_size)
        minimum = [math.inf, math.inf, math.inf]
        maximum = [-math.inf, -math.inf, -math.inf]
        record_index = 0
        while record_index < chunk.count:
            record_count = min(records_per_read, chunk.count - record_index)
            byte_count = record_count * record_size
            offset = header.payload_offset + record_index * record_size
            block = _read_exact_at(self._fd, byte_count, offset)
            for values in struct.iter_unpack("<18f", block):
                if not all(math.isfinite(value) for value in values):
                    raise InvalidViewerSource("compressed PLY chunk bounds are non-finite")
                position_min = values[0:3]
                position_max = values[3:6]
                scale_min = values[6:9]
                scale_max = values[9:12]
                color_min = values[12:15]
                color_max = values[15:18]
                for lower, upper in (
                    (position_min, position_max),
                    (scale_min, scale_max),
                    (color_min, color_max),
                ):
                    if any(low > high for low, high in zip(lower, upper)):
                        raise InvalidViewerSource("compressed PLY chunk bounds are inverted")
                try:
                    scale_extent = math.exp(max(scale_max))
                except OverflowError as exc:
                    raise InvalidViewerSource(
                        "compressed PLY scale bounds overflow"
                    ) from exc
                if not math.isfinite(scale_extent) or scale_extent > _FLOAT32_MAX:
                    raise InvalidViewerSource("compressed PLY scale bounds overflow")
                if any(
                    not math.isfinite((value - 0.5) / _SH_C0)
                    or abs((value - 0.5) / _SH_C0) > _FLOAT32_MAX
                    for value in (*color_min, *color_max)
                ):
                    raise InvalidViewerSource("compressed PLY color bounds overflow")
                for axis in range(3):
                    expanded_min = position_min[axis] - scale_extent
                    expanded_max = position_max[axis] + scale_extent
                    if not math.isfinite(expanded_min) or not math.isfinite(
                        expanded_max
                    ):
                        raise InvalidViewerSource("compressed PLY render bounds overflow")
                    minimum[axis] = min(minimum[axis], expanded_min)
                    maximum[axis] = max(maximum[axis], expanded_max)
            record_index += record_count

        vertex_record_size = len(PACKED_VERTEX_PROPERTIES) * 4
        records_per_read = max(1, STREAM_CHUNK_BYTES // vertex_record_size)
        vertex_offset = header.payload_offset + chunk.count * record_size
        record_index = 0
        rotation_norm = 1.0 / (math.sqrt(2.0) * 0.5)
        while record_index < vertex.count:
            record_count = min(records_per_read, vertex.count - record_index)
            byte_count = record_count * vertex_record_size
            offset = vertex_offset + record_index * vertex_record_size
            block = _read_exact_at(self._fd, byte_count, offset)
            for _position, packed_rotation, _scale, _color in struct.iter_unpack(
                "<4I", block
            ):
                a = (((packed_rotation >> 20) & 0x3FF) / 1023.0 - 0.5) * rotation_norm
                b = (((packed_rotation >> 10) & 0x3FF) / 1023.0 - 0.5) * rotation_norm
                c = ((packed_rotation & 0x3FF) / 1023.0 - 0.5) * rotation_norm
                missing_squared = 1.0 - (a * a + b * b + c * c)
                if not math.isfinite(missing_squared) or missing_squared < 0.0:
                    raise InvalidViewerSource("compressed PLY packed rotation is invalid")
                if not math.isfinite(math.sqrt(missing_squared)):
                    raise InvalidViewerSource("compressed PLY packed rotation is invalid")
            record_index += record_count
        self._assert_stable()
        return ViewerInspection(
            gaussian_count=vertex.count,
            chunk_count=chunk.count,
            payload_offset=header.payload_offset,
            scene_aabb_min=(minimum[0], minimum[1], minimum[2]),
            scene_aabb_max=(maximum[0], maximum[1], maximum[2]),
        )

    def stream_to(self, destination: BinaryIO, expected: ViewerFingerprint) -> None:
        """Write exact bytes in bounded chunks, withholding the last until verified."""

        self._assert_stable()
        digest = hashlib.sha256()
        offset = 0
        pending: bytes | None = None
        while offset < expected.size_bytes:
            requested = min(STREAM_CHUNK_BYTES, expected.size_bytes - offset)
            block = _read_exact_at(self._fd, requested, offset)
            digest.update(block)
            if pending is not None:
                destination.write(pending)
            pending = block
            offset += len(block)
        if os.pread(self._fd, 1, expected.size_bytes):
            raise ViewerSourceChanged("source grew during streaming")
        self._assert_stable()
        if digest.hexdigest() != expected.sha256:
            raise ViewerSourceChanged("source content changed during streaming")
        if pending is None:
            raise InvalidViewerSource("compressed PLY source is empty")
        destination.write(pending)


@contextmanager
def open_viewer_source(
    sources: Mapping[str, PinnedViewerSource], scene_id: str
) -> Iterator[OpenViewerSource]:
    """Open one configured canonical target and enforce its pinned identity."""

    scene_id = validate_scene_id(scene_id)
    config = sources.get(scene_id)
    if (
        config is None
        or config.canonical_path is None
        or config.pinned_identity is None
    ):
        raise ViewerSourceUnavailable("viewer scene is unavailable")
    try:
        fd = _open_canonical_path(config.canonical_path)
    except OSError as exc:
        raise ViewerSourceUnavailable("viewer scene is unavailable") from exc
    try:
        descriptor_info = os.fstat(fd)
        opened_identity = _identity(descriptor_info)
        if (
            not stat.S_ISREG(descriptor_info.st_mode)
            or opened_identity != config.pinned_identity
        ):
            raise ViewerSourceUnavailable("viewer scene is unavailable")
        yield OpenViewerSource(scene_id, config, fd, opened_identity)
    finally:
        os.close(fd)


def build_viewer_manifest(
    sources: Mapping[str, PinnedViewerSource], scene_id: str
) -> ViewerManifest:
    """Validate and fingerprint one immutable allowlisted compressed source."""

    with open_viewer_source(sources, scene_id) as source:
        before = source.fingerprint()
        inspection = source.inspect()
        after = source.fingerprint()
        if before != after:
            raise ViewerSourceChanged("source content changed during manifest creation")
        return ViewerManifest(
            scene_id=scene_id,
            fingerprint=before,
            inspection=inspection,
            coordinate_system=source.config.coordinate_system,
        )


@contextmanager
def open_viewer_stream(
    sources: Mapping[str, PinnedViewerSource], scene_id: str
) -> Iterator[tuple[OpenViewerSource, ViewerFingerprint]]:
    """Prepare a schema-validated descriptor and digest for an HTTP stream."""

    with open_viewer_source(sources, scene_id) as source:
        source.inspect()
        fingerprint = source.fingerprint()
        source._assert_stable()
        yield source, fingerprint
