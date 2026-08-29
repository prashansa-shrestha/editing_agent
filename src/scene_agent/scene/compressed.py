"""Strict inspector for PlayCanvas/splat-transform compressed PLY files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Union

from .errors import PLYPayloadError, PLYSchemaError
from .ply import PLYHeader, PLYSource, PLYSourceData, source_data


PathLike = Union[str, Path]
CHUNK_SIZE = 256

CHUNK_PROPERTIES: tuple[str, ...] = (
    "min_x",
    "min_y",
    "min_z",
    "max_x",
    "max_y",
    "max_z",
    "min_scale_x",
    "min_scale_y",
    "min_scale_z",
    "max_scale_x",
    "max_scale_y",
    "max_scale_z",
    "min_r",
    "min_g",
    "min_b",
    "max_r",
    "max_g",
    "max_b",
)
PACKED_VERTEX_PROPERTIES: tuple[str, ...] = (
    "packed_position",
    "packed_rotation",
    "packed_scale",
    "packed_color",
)
SH_PROPERTIES: tuple[str, ...] = tuple(f"f_rest_{index}" for index in range(45))


@dataclass(frozen=True)
class CompressedInspection:
    """Validated compressed source layout and exact payload sizing."""

    header: PLYHeader
    chunk_count: int
    vertex_count: int
    sh_count: int
    payload_offset: int
    expected_payload_bytes: int
    actual_payload_bytes: int
    chunk_record_size: int = len(CHUNK_PROPERTIES) * 4
    vertex_record_size: int = len(PACKED_VERTEX_PROPERTIES) * 4
    sh_record_size: int = len(SH_PROPERTIES)

    @property
    def gaussian_count(self) -> int:
        return self.vertex_count

    @property
    def payload_length(self) -> int:
        return self.actual_payload_bytes

    @property
    def chunk_size(self) -> int:
        return CHUNK_SIZE

    def as_dict(self) -> dict[str, object]:
        return {
            "chunk_count": self.chunk_count,
            "vertex_count": self.vertex_count,
            "gaussian_count": self.vertex_count,
            "sh_count": self.sh_count,
            "payload_offset": self.payload_offset,
            "expected_payload_bytes": self.expected_payload_bytes,
            "actual_payload_bytes": self.actual_payload_bytes,
            "chunk_size": CHUNK_SIZE,
        }


def _expect_element(element, name: str, count: int, properties: tuple[str, ...], type_name: str) -> None:
    if element.name != name:
        raise PLYSchemaError(f"compressed PLY expected element {name!r}, found {element.name!r}")
    if element.count != count and count >= 0:
        raise PLYSchemaError(
            f"compressed PLY element {name!r} has count {element.count}, expected {count}"
        )
    actual = tuple(prop.name for prop in element.properties)
    expected = properties
    if actual != expected:
        raise PLYSchemaError(
            f"compressed PLY {name!r} property order mismatch: expected {expected}, found {actual}"
        )
    for prop in element.properties:
        if prop.is_list:
            raise PLYSchemaError(
                f"compressed PLY rejects list property {prop.name!r}"
            )
        if prop.type_name != type_name:
            raise PLYSchemaError(
                f"compressed PLY property {prop.name!r} must have type {type_name}, found {prop.type_name}"
            )


def inspect_compressed_ply(source: PLYSource) -> CompressedInspection:
    """Validate the exact known splat-transform compressed PLY layout.

    The accepted source has three fixed-width elements in this exact order:
    18 little-endian float chunk bounds, four uint packed vertex fields, and
    45 uchar higher-order SH fields.  The payload must contain exactly the
    bytes implied by the declared counts; no extra bytes or variable/list
    records are accepted.
    """

    info: PLYSourceData = source_data(source)
    header = info.header
    if header.format_name != "binary_little_endian" or header.version != "1.0":
        raise PLYSchemaError("compressed PLY must use binary_little_endian 1.0")
    if len(header.elements) != 3:
        names = ", ".join(element.name for element in header.elements)
        raise PLYSchemaError(
            f"compressed PLY requires exactly chunk, vertex, and sh elements; found {names or '<none>'}"
        )
    chunk, vertex, sh = header.elements
    if chunk.count <= 0 or vertex.count <= 0 or sh.count <= 0:
        raise PLYSchemaError(
            "compressed PLY chunk, vertex, and sh counts must all be positive"
        )
    expected_chunks = (vertex.count + CHUNK_SIZE - 1) // CHUNK_SIZE
    _expect_element(chunk, "chunk", expected_chunks, CHUNK_PROPERTIES, "float")
    _expect_element(vertex, "vertex", -1, PACKED_VERTEX_PROPERTIES, "uint")
    _expect_element(sh, "sh", vertex.count, SH_PROPERTIES, "uchar")

    expected = (
        chunk.count * len(CHUNK_PROPERTIES) * 4
        + vertex.count * len(PACKED_VERTEX_PROPERTIES) * 4
        + sh.count * len(SH_PROPERTIES)
    )
    actual = info.size_bytes - header.payload_offset
    if actual < expected:
        raise PLYPayloadError(
            f"compressed PLY payload is truncated: expected {expected} bytes, found {actual}"
        )
    if actual > expected:
        raise PLYPayloadError(
            f"compressed PLY payload has trailing bytes: expected {expected} bytes, found {actual}"
        )
    return CompressedInspection(
        header=header,
        chunk_count=chunk.count,
        vertex_count=vertex.count,
        sh_count=sh.count,
        payload_offset=header.payload_offset,
        expected_payload_bytes=expected,
        actual_payload_bytes=actual,
    )


validate_compressed_ply = inspect_compressed_ply
