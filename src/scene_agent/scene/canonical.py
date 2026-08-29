"""Canonical uncompressed Gaussian PLY validation, loading, and writing."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Sequence, Union

import numpy as np

from .errors import OutputExistsError, PLYPayloadError, PLYSchemaError
from .ply import (
    PLYElement,
    PLYHeader,
    PLYProperty,
    PLYSource,
    PLYSourceData,
    MAX_HEADER_BYTES,
    parse_ply_header,
    read_element_array,
    render_header,
    source_data,
    structured_dtype,
)
from .paths import OwnedOutputFile, open_secure_output_target


PathLike = Union[str, Path]

CANONICAL_PROPERTY_NAMES: tuple[str, ...] = (
    "x",
    "y",
    "z",
    "f_dc_0",
    "f_dc_1",
    "f_dc_2",
    *(f"f_rest_{index}" for index in range(45)),
    "opacity",
    "scale_0",
    "scale_1",
    "scale_2",
    "rot_0",
    "rot_1",
    "rot_2",
    "rot_3",
)
CANONICAL_FLOAT_TYPE_NAMES = frozenset({"float", "float32"})
# Keep the byte estimate derived from the schema so a field-count correction
# cannot silently leave the decoder's memory guard stale.
CANONICAL_FIELD_COUNT = len(CANONICAL_PROPERTY_NAMES)
CANONICAL_REQUIRED_RECORD_BYTES = len(CANONICAL_PROPERTY_NAMES) * 4
CANONICAL_RECORD_BYTES = CANONICAL_REQUIRED_RECORD_BYTES
HARD_ARRAY_BYTES = 1 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class CanonicalInspection:
    """Validated canonical schema and fixed payload sizing."""

    header: PLYHeader
    vertex_count: int
    payload_offset: int
    expected_payload_bytes: int
    actual_payload_bytes: int
    property_names: tuple[str, ...]
    unknown_property_names: tuple[str, ...]

    @property
    def gaussian_count(self) -> int:
        return self.vertex_count

    @property
    def record_size(self) -> int:
        return self.expected_payload_bytes // self.vertex_count if self.vertex_count else 0

    def as_dict(self) -> dict[str, object]:
        return {
            "vertex_count": self.vertex_count,
            "gaussian_count": self.vertex_count,
            "payload_offset": self.payload_offset,
            "expected_payload_bytes": self.expected_payload_bytes,
            "actual_payload_bytes": self.actual_payload_bytes,
            "property_names": self.property_names,
            "unknown_property_names": self.unknown_property_names,
        }


@dataclass
class CanonicalScene:
    """One canonical Gaussian table backed by a packed structured array.

    ``data`` has one row per Gaussian and one field per PLY scalar property.
    Required canonical fields are exposed as views where possible; unknown
    scalar fields stay in the same structured array and are therefore retained
    during a write.  The stable Gaussian identifier is the zero-based row
    index and is never serialized as a synthetic property.
    """

    data: np.ndarray
    header: PLYHeader
    source_path: Path | None = None
    source_sha256: str | None = None
    coordinate_system: str = "unknown"

    def __post_init__(self) -> None:
        if not isinstance(self.data, np.ndarray) or self.data.dtype.names is None:
            raise TypeError("CanonicalScene.data must be a structured NumPy array")
        if self.data.ndim != 1:
            raise ValueError("CanonicalScene.data must be one-dimensional")
        if len(self.header.elements) != 1 or self.header.elements[0].name != "vertex":
            raise PLYSchemaError("CanonicalScene requires exactly one vertex element")
        element = self.header.elements[0]
        expected_dtype = structured_dtype(element)
        if self.data.dtype != expected_dtype:
            raise PLYSchemaError(
                f"array dtype does not match canonical header: {self.data.dtype!r} != {expected_dtype!r}"
            )
        if self.data.shape[0] != element.count:
            raise PLYSchemaError("array length does not match vertex count")

    @property
    def element(self) -> PLYElement:
        return self.header.elements[0]

    @property
    def properties(self) -> tuple[PLYProperty, ...]:
        return self.element.properties

    @property
    def property_names(self) -> tuple[str, ...]:
        return self.element.property_names

    @property
    def dtype(self) -> np.dtype:
        return self.data.dtype

    @property
    def gaussian_ids(self) -> np.ndarray:
        return np.arange(self.data.shape[0], dtype=np.int64)

    @property
    def gaussian_count(self) -> int:
        return int(self.data.shape[0])

    def column(self, name: str) -> np.ndarray:
        """Return one field view without copying the whole Gaussian table."""

        if name not in self.data.dtype.names:
            raise KeyError(name)
        return self.data[name]

    @property
    def positions(self) -> np.ndarray:
        return _columns(self.data, ("x", "y", "z"))

    @property
    def f_dc(self) -> np.ndarray:
        return _columns(self.data, ("f_dc_0", "f_dc_1", "f_dc_2"))

    @property
    def f_rest(self) -> np.ndarray:
        return _columns(self.data, tuple(f"f_rest_{i}" for i in range(45)))

    @property
    def opacity(self) -> np.ndarray:
        return self.column("opacity")

    @property
    def scales(self) -> np.ndarray:
        return _columns(self.data, ("scale_0", "scale_1", "scale_2"))

    @property
    def rotations(self) -> np.ndarray:
        return _columns(self.data, ("rot_0", "rot_1", "rot_2", "rot_3"))

    @property
    def property_digest(self) -> dict[str, str]:
        return {name: _array_digest(self.data[name]) for name in self.property_names}

    def property_digests(self) -> dict[str, str]:
        """Method alias for callers following the original conceptual API."""

        return self.property_digest

    def clone(self) -> "CanonicalScene":
        return CanonicalScene(
            data=self.data.copy(),
            header=self.header,
            source_path=None,
            source_sha256=self.source_sha256,
            coordinate_system=self.coordinate_system,
        )

    def save(
        self,
        path: PathLike,
        *,
        repository_root: PathLike | None = None,
        refuse_existing: bool = True,
    ) -> Path:
        return write_canonical_ply(
            self,
            path,
            repository_root=repository_root,
            refuse_existing=refuse_existing,
        )

    def __len__(self) -> int:
        return self.gaussian_count


@dataclass(frozen=True)
class CanonicalComparison:
    """Result of comparing canonical schemas and row values."""

    equal: bool
    same_schema: bool
    same_values: bool
    same_header_bytes: bool
    left_count: int
    right_count: int
    differences: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.equal

    @property
    def exact(self) -> bool:
        return self.equal

    def as_dict(self) -> dict[str, object]:
        return {
            "equal": self.equal,
            "same_schema": self.same_schema,
            "same_values": self.same_values,
            "same_header_bytes": self.same_header_bytes,
            "left_count": self.left_count,
            "right_count": self.right_count,
            "differences": self.differences,
        }


def _columns(data: np.ndarray, names: Sequence[str]) -> np.ndarray:
    # A packed canonical table has these fields contiguous in the usual case.
    # For unknown properties inserted between required fields, NumPy cannot
    # expose a 2-D view without a copy; this localizes that unavoidable copy to
    # a convenience property rather than copying the complete table at load.
    return np.column_stack([data[name] for name in names])


def _array_digest(array: np.ndarray, *, chunk_rows: int = 65536) -> str:
    digest = hashlib.sha256()
    if array.ndim == 0:
        digest.update(np.ascontiguousarray(array).tobytes())
        return digest.hexdigest()
    for start in range(0, array.shape[0], chunk_rows):
        block = np.ascontiguousarray(array[start : start + chunk_rows])
        digest.update(block.tobytes(order="C"))
    return digest.hexdigest()


def _validate_required_properties(element: PLYElement) -> tuple[str, ...]:
    if not element.properties:
        raise PLYSchemaError("canonical vertex element has no properties")
    unknown: list[str] = []
    required_index = 0
    for prop in element.properties:
        if prop.is_list:
            raise PLYSchemaError(
                f"canonical schema rejects list property {prop.name!r}"
            )
        if prop.name in CANONICAL_PROPERTY_NAMES:
            if required_index >= len(CANONICAL_PROPERTY_NAMES):
                raise PLYSchemaError(f"unexpected duplicate canonical property {prop.name!r}")
            expected_name = CANONICAL_PROPERTY_NAMES[required_index]
            if prop.name != expected_name:
                raise PLYSchemaError(
                    f"canonical property order mismatch: expected {expected_name!r}, found {prop.name!r}"
                )
            if prop.type_name not in CANONICAL_FLOAT_TYPE_NAMES:
                raise PLYSchemaError(
                    f"canonical property {prop.name!r} must be float32, found {prop.type_name!r}"
                )
            required_index += 1
        else:
            unknown.append(prop.name)
    if required_index != len(CANONICAL_PROPERTY_NAMES):
        missing = CANONICAL_PROPERTY_NAMES[required_index:]
        raise PLYSchemaError(f"canonical schema is missing properties: {', '.join(missing)}")
    return tuple(unknown)


def _canonical_info(source: PLYSource) -> tuple[PLYSourceData, CanonicalInspection]:
    info = source_data(source)
    return _canonical_info_from_data(info)


def _canonical_info_from_data(
    info: PLYSourceData,
) -> tuple[PLYSourceData, CanonicalInspection]:
    if info.header.format_name != "binary_little_endian" or info.header.version != "1.0":
        raise PLYSchemaError("canonical PLY must use binary_little_endian 1.0")
    if len(info.header.elements) != 1:
        names = ", ".join(element.name for element in info.header.elements)
        raise PLYSchemaError(
            f"canonical PLY supports exactly one vertex element; found {names or '<none>'}"
        )
    element = info.header.elements[0]
    if element.name != "vertex":
        raise PLYSchemaError(f"canonical PLY element must be 'vertex', found {element.name!r}")
    unknown = _validate_required_properties(element)
    record_size = element.record_size
    expected = element.count * record_size
    actual = info.size_bytes - info.header.payload_offset
    if actual < expected:
        raise PLYPayloadError(
            f"canonical payload is truncated: expected {expected} bytes, found {actual}"
        )
    if actual != expected:
        raise PLYPayloadError(
            f"canonical payload has trailing bytes: expected {expected} bytes, found {actual}"
        )
    if expected > HARD_ARRAY_BYTES:
        raise PLYPayloadError(
            f"canonical payload is {expected} bytes, above the 1 GiB hard memory limit"
        )
    inspection = CanonicalInspection(
        header=info.header,
        vertex_count=element.count,
        payload_offset=info.header.payload_offset,
        expected_payload_bytes=expected,
        actual_payload_bytes=actual,
        property_names=element.property_names,
        unknown_property_names=unknown,
    )
    return info, inspection


def validate_canonical_ply(
    source: PLYSource,
    *,
    reject_nonfinite: bool = False,
) -> CanonicalInspection:
    """Validate canonical schema, payload length, and optionally finite values."""

    info, inspection = _canonical_info(source)
    if reject_nonfinite:
        array = read_element_array(info, info.header.elements[0], require_exact_payload=True)
        for name in inspection.property_names:
            field = array[name]
            if np.issubdtype(field.dtype, np.floating) and not np.isfinite(field).all():
                raise PLYSchemaError(
                    f"canonical property {name!r} contains NaN or infinity"
                )
    return inspection


validate_canonical_schema = validate_canonical_ply


def load_canonical_ply(
    source: PLYSource,
    *,
    coordinate_system: str = "unknown",
    reject_nonfinite: bool = False,
) -> CanonicalScene:
    """Load a canonical PLY into one packed structured NumPy array."""

    info, inspection = _canonical_info(source)
    element = info.header.elements[0]
    array = read_element_array(info, element, require_exact_payload=True)
    if array.shape[0] != inspection.vertex_count:
        raise PLYPayloadError("loaded row count differs from validated vertex count")
    if reject_nonfinite:
        for name in inspection.property_names:
            field = array[name]
            if np.issubdtype(field.dtype, np.floating) and not np.isfinite(field).all():
                raise PLYSchemaError(f"canonical property {name!r} contains NaN or infinity")
    source_path = info.path
    source_digest: str | None = None
    if source_path is not None:
        # Hashing is intentionally opt-in at the scene layer because a caller
        # loading many working files may already have a fingerprint.
        source_digest = None
    return CanonicalScene(
        data=array,
        header=info.header,
        source_path=source_path,
        source_sha256=source_digest,
        coordinate_system=coordinate_system,
    )


read_canonical_ply = load_canonical_ply


def _coerce_scene(scene_or_path: CanonicalScene | PLYSource) -> CanonicalScene:
    if isinstance(scene_or_path, CanonicalScene):
        return scene_or_path
    return load_canonical_ply(scene_or_path)


def _header_for_scene(scene: CanonicalScene) -> bytes:
    # A loaded header's raw bytes retain comments, CRLF choice, and exact
    # declaration spelling.  For a constructed scene render the schema.
    if scene.header.raw_bytes:
        return scene.header.raw_bytes
    return render_header(scene.header.elements, comments=scene.header.comments)


def _source_data_from_fd(fd: int) -> PLYSourceData:
    """Read header and size from the retained inode, never from its pathname."""

    file_info = os.fstat(fd)
    prefix = os.pread(fd, MAX_HEADER_BYTES + 1, 0)
    match = re.search(rb"(?m)^end_header\r?\n", prefix)
    if match is None:
        raise PLYSchemaError("canonical partial has no complete end_header line")
    header = parse_ply_header(prefix[: match.end()])
    return PLYSourceData(header=header, size_bytes=file_info.st_size)


def validate_canonical_file_descriptor(
    fd: int,
    *,
    reject_nonfinite: bool = False,
) -> CanonicalInspection:
    """Validate the exact regular-file inode retained by ``fd``."""

    file_info = os.fstat(fd)
    if not stat.S_ISREG(file_info.st_mode):
        raise PLYSchemaError("canonical descriptor is not a regular file")
    info, inspection = _canonical_info_from_data(_source_data_from_fd(fd))
    if reject_nonfinite:
        dtype = structured_dtype(info.header.elements[0])
        rows_per_chunk = max(1, (4 * 1024 * 1024) // int(dtype.itemsize))
        for start in range(0, inspection.vertex_count, rows_per_chunk):
            rows = min(rows_per_chunk, inspection.vertex_count - start)
            byte_count = rows * int(dtype.itemsize)
            offset = info.header.payload_offset + start * int(dtype.itemsize)
            payload = os.pread(fd, byte_count, offset)
            if len(payload) != byte_count:
                raise PLYPayloadError("canonical descriptor payload changed during validation")
            array = np.frombuffer(payload, dtype=dtype, count=rows)
            for name in inspection.property_names:
                field = array[name]
                if np.issubdtype(field.dtype, np.floating) and not np.isfinite(field).all():
                    raise PLYSchemaError(
                        f"canonical property {name!r} contains NaN or infinity"
                    )
    if os.fstat(fd).st_size != file_info.st_size:
        raise PLYPayloadError("canonical descriptor size changed during validation")
    return inspection


def _validate_written_partial(scene: CanonicalScene, partial: OwnedOutputFile) -> None:
    """Validate schema, finiteness, and bytes through the retained partial fd."""

    partial.assert_same_inode()
    try:
        inspection = validate_canonical_file_descriptor(
            partial.fd,
            reject_nonfinite=True,
        )
        info = _source_data_from_fd(partial.fd)
    except Exception as exc:
        raise PLYSchemaError(
            f"canonical partial failed schema or payload validation: {exc}"
        ) from exc
    if inspection.vertex_count != scene.gaussian_count:
        raise PLYSchemaError(
            "canonical partial row count differs from the source scene: "
            f"{inspection.vertex_count} != {scene.gaussian_count}"
        )
    partial_signature = tuple(
        (prop.name, prop.type_name, prop.list_count_type, prop.list_item_type)
        for prop in info.header.elements[0].properties
    )
    if partial_signature != _schema_signature(scene):
        raise PLYSchemaError(
            "canonical partial properties/types differ from the source scene"
        )
    dtype = structured_dtype(info.header.elements[0])
    rows_per_chunk = max(1, (4 * 1024 * 1024) // int(dtype.itemsize))
    for start in range(0, scene.gaussian_count, rows_per_chunk):
        stop = min(scene.gaussian_count, start + rows_per_chunk)
        expected = scene.data[start:stop].tobytes(order="C")
        offset = info.header.payload_offset + start * int(dtype.itemsize)
        actual = os.pread(partial.fd, len(expected), offset)
        if actual != expected:
            raise PLYSchemaError(
                "canonical partial property values differ from the source scene"
            )
        rows = np.frombuffer(actual, dtype=dtype, count=stop - start)
        for name in inspection.property_names:
            field = rows[name]
            if np.issubdtype(field.dtype, np.floating) and not np.isfinite(field).all():
                raise PLYSchemaError(
                    f"canonical partial property {name!r} contains NaN or infinity"
                )


def _atomic_write_scene(scene: CanonicalScene, target) -> None:
    partial = target.create_partial()
    completed = False
    try:
        with os.fdopen(os.dup(partial.fd), "wb") as handle:
            handle.write(_header_for_scene(scene))
            scene.data.tofile(handle)
            handle.flush()
            os.fsync(handle.fileno())
        _validate_written_partial(scene, partial)
        target.publish(partial)
        target.unlink_owned(partial.name, partial.identity)
        completed = True
    finally:
        if not completed:
            target.unlink_owned(target.name, partial.identity)
        target.unlink_owned(partial.name, partial.identity)
        partial.close()


def write_canonical_ply(
    scene_or_path: CanonicalScene | PLYSource,
    destination: PathLike,
    *,
    repository_root: PathLike | None = None,
    refuse_existing: bool = True,
) -> Path:
    """Write a canonical scene beneath the Milestone 1 output root.

    Even an absolute path such as ``/tmp/scene.ply`` is rejected by the
    repository path policy.  The destination is always beneath the real
    repository ``outputs/milestone1`` directory.
    """

    scene = _coerce_scene(scene_or_path)
    with open_secure_output_target(
        destination,
        repository_root=repository_root,
        create_parent=True,
        refuse_existing=refuse_existing,
    ) as target:
        if scene.source_path is not None and target.path == scene.source_path.resolve(strict=False):
            raise OutputExistsError("refusing to write a canonical scene over its source")
        _atomic_write_scene(scene, target)
        return target.path


def _schema_signature(scene: CanonicalScene) -> tuple[object, ...]:
    return tuple(
        (prop.name, prop.type_name, prop.list_count_type, prop.list_item_type)
        for prop in scene.properties
    )


def compare_canonical_ply(
    left: CanonicalScene | PLYSource,
    right: CanonicalScene | PLYSource,
    *,
    exact: bool = True,
    rtol: float = 0.0,
    atol: float = 0.0,
) -> CanonicalComparison:
    """Compare schema and values, retaining unknown scalar properties."""

    left_scene = _coerce_scene(left)
    right_scene = _coerce_scene(right)
    differences: list[str] = []
    same_schema = (
        left_scene.header.format_name == right_scene.header.format_name
        and left_scene.header.version == right_scene.header.version
        and _schema_signature(left_scene) == _schema_signature(right_scene)
    )
    if not same_schema:
        differences.append("schema")
    same_count = left_scene.gaussian_count == right_scene.gaussian_count
    if not same_count:
        differences.append("row_count")
    same_values = False
    if same_schema and same_count:
        if exact:
            same_values = _array_digest(left_scene.data) == _array_digest(right_scene.data)
        else:
            same_values = bool(
                np.allclose(
                    left_scene.data.view(np.uint8),
                    right_scene.data.view(np.uint8),
                    rtol=rtol,
                    atol=atol,
                    equal_nan=True,
                )
            )
    if not same_values:
        differences.append("values")
    same_header = left_scene.header.raw_bytes == right_scene.header.raw_bytes
    return CanonicalComparison(
        equal=same_schema and same_count and same_values,
        same_schema=same_schema,
        same_values=same_values,
        same_header_bytes=same_header,
        left_count=left_scene.gaussian_count,
        right_count=right_scene.gaussian_count,
        differences=tuple(differences),
    )


compare_scenes = compare_canonical_ply
