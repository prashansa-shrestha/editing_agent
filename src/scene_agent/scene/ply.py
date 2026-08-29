"""Strict, dependency-light binary PLY parsing primitives.

Only the small subset needed by Gaussian Splatting is accepted by the public
validators.  The generic header parser still understands scalar PLY types so
unknown scalar properties can be carried through a canonical round trip.
"""

from __future__ import annotations

from dataclasses import dataclass
import io
import re
from pathlib import Path
from typing import BinaryIO, Iterable, Mapping, Sequence, Union

import numpy as np

from .errors import PLYHeaderError, PLYPayloadError, PLYSchemaError


PathLike = Union[str, Path]
PLYSource = Union[PathLike, bytes, bytearray, memoryview, BinaryIO]
MAX_HEADER_BYTES = 16 * 1024 * 1024
MAX_ELEMENT_COUNT = (1 << 63) - 1

# PLY's standard scalar names, plus the width-explicit spellings emitted by a
# few tools.  The original token is retained on PLYProperty for exact header
# preservation; NumPy always reads/writes the little-endian representation.
SCALAR_DTYPES: dict[str, np.dtype] = {
    "char": np.dtype("<i1"),
    "int8": np.dtype("<i1"),
    "uchar": np.dtype("<u1"),
    "uint8": np.dtype("<u1"),
    "short": np.dtype("<i2"),
    "int16": np.dtype("<i2"),
    "ushort": np.dtype("<u2"),
    "uint16": np.dtype("<u2"),
    "int": np.dtype("<i4"),
    "int32": np.dtype("<i4"),
    "uint": np.dtype("<u4"),
    "uint32": np.dtype("<u4"),
    "long": np.dtype("<i8"),
    "int64": np.dtype("<i8"),
    "ulong": np.dtype("<u8"),
    "uint64": np.dtype("<u8"),
    "float": np.dtype("<f4"),
    "float32": np.dtype("<f4"),
    "double": np.dtype("<f8"),
    "float64": np.dtype("<f8"),
}


@dataclass(frozen=True)
class PLYProperty:
    """One PLY property declaration in header order."""

    name: str
    type_name: str | None = None
    list_count_type: str | None = None
    list_item_type: str | None = None

    @property
    def is_list(self) -> bool:
        return self.list_count_type is not None

    @property
    def scalar_dtype(self) -> np.dtype:
        if self.is_list or self.type_name is None:
            raise PLYSchemaError(f"property {self.name!r} is not a scalar property")
        try:
            return SCALAR_DTYPES[self.type_name]
        except KeyError as exc:
            raise PLYSchemaError(
                f"unsupported scalar type {self.type_name!r} for property {self.name!r}"
            ) from exc

    @property
    def item_size(self) -> int:
        return int(self.scalar_dtype.itemsize)

    def declaration(self) -> str:
        if self.is_list:
            assert self.list_count_type is not None
            assert self.list_item_type is not None
            return f"property list {self.list_count_type} {self.list_item_type} {self.name}"
        assert self.type_name is not None
        return f"property {self.type_name} {self.name}"


@dataclass(frozen=True)
class PLYElement:
    """One PLY element declaration and its properties."""

    name: str
    count: int
    properties: tuple[PLYProperty, ...]

    @property
    def record_size(self) -> int:
        if any(prop.is_list for prop in self.properties):
            raise PLYSchemaError(
                f"element {self.name!r} contains list properties; fixed record size is undefined"
            )
        return sum(prop.item_size for prop in self.properties)

    @property
    def property_names(self) -> tuple[str, ...]:
        return tuple(prop.name for prop in self.properties)


@dataclass(frozen=True)
class PLYHeader:
    """Parsed header, including the byte offset at which binary data starts."""

    elements: tuple[PLYElement, ...]
    raw_bytes: bytes
    payload_offset: int
    format_name: str = "binary_little_endian"
    version: str = "1.0"
    comments: tuple[str, ...] = ()

    def element(self, name: str) -> PLYElement:
        for element in self.elements:
            if element.name == name:
                return element
        raise PLYSchemaError(f"PLY element {name!r} is not present")

    def render(self) -> bytes:
        """Render a normalized header for a newly constructed schema."""

        lines = ["ply", f"format {self.format_name} {self.version}"]
        lines.extend(self.comments)
        for element in self.elements:
            lines.append(f"element {element.name} {element.count}")
            lines.extend(prop.declaration() for prop in element.properties)
        lines.append("end_header")
        return ("\n".join(lines) + "\n").encode("ascii")


@dataclass(frozen=True)
class PLYSourceData:
    """Header and source-size information without retaining a whole file."""

    header: PLYHeader
    size_bytes: int
    in_memory: bytes | None = None
    path: Path | None = None


def _header_end(blob: bytes) -> int | None:
    # Anchoring at a line start prevents a payload/comment containing the word
    # ``end_header`` from being mistaken for the terminator.
    match = re.search(rb"(?m)^end_header\r?\n", blob)
    if match is None:
        return None
    return match.end()


def _read_header_blob(source: PLYSource) -> PLYSourceData:
    if isinstance(source, (str, Path)):
        path = Path(source).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"PLY source does not exist or is not a regular file: {path}")
        size = path.stat().st_size
        with path.open("rb") as handle:
            blob = handle.read(MAX_HEADER_BYTES + 1)
        end = _header_end(blob)
        if end is None:
            if len(blob) > MAX_HEADER_BYTES:
                raise PLYHeaderError(
                    f"PLY header exceeds the {MAX_HEADER_BYTES}-byte safety limit: {path}"
                )
            raise PLYHeaderError(f"PLY header has no complete end_header line: {path}")
        header = parse_ply_header(blob[:end])
        return PLYSourceData(header=header, size_bytes=size, path=path.resolve())

    if isinstance(source, memoryview):
        blob = source.tobytes()
    elif isinstance(source, bytearray):
        blob = bytes(source)
    elif isinstance(source, bytes):
        blob = source
    elif hasattr(source, "read"):
        blob = source.read(MAX_HEADER_BYTES + 1)
        if not isinstance(blob, bytes):
            blob = bytes(blob)
    else:
        raise TypeError(f"unsupported PLY source type: {type(source)!r}")
    end = _header_end(blob)
    if end is None:
        if len(blob) > MAX_HEADER_BYTES:
            raise PLYHeaderError(
                f"PLY header exceeds the {MAX_HEADER_BYTES}-byte safety limit"
            )
        raise PLYHeaderError("PLY header has no complete end_header line")
    # A bytes/BytesIO source may include all payload bytes.  For a stream where
    # only the prefix was supplied, the size is necessarily the observed size;
    # callers needing strict payload sizing should pass a path or full bytes.
    header = parse_ply_header(blob[:end])
    return PLYSourceData(header=header, size_bytes=len(blob), in_memory=blob)


def parse_ply_header(header_bytes: bytes | bytearray | memoryview) -> PLYHeader:
    """Parse and validate a binary PLY header from bytes.

    Comments and blank lines are retained, while unknown structural directives
    are rejected.  This is intentionally stricter than a general-purpose PLY
    reader because the decoder boundary must not guess a binary layout.
    """

    blob = bytes(header_bytes)
    end = _header_end(blob)
    if end is None or end != len(blob):
        raise PLYHeaderError("header bytes must end immediately after end_header newline")
    raw_lines = blob.splitlines(keepends=True)
    if not raw_lines:
        raise PLYHeaderError("empty PLY header")
    first = raw_lines[0].decode("ascii", errors="strict").rstrip("\r\n")
    if first != "ply":
        raise PLYHeaderError("PLY header must begin with the exact 'ply' magic line")

    format_seen = False
    format_name = ""
    version = ""
    elements: list[PLYElement] = []
    active_name: str | None = None
    active_count: int | None = None
    active_properties: list[PLYProperty] = []
    comments: list[str] = []
    end_seen = False

    def finish_element() -> None:
        nonlocal active_name, active_count, active_properties
        if active_name is not None:
            assert active_count is not None
            if any(el.name == active_name for el in elements):
                raise PLYHeaderError(f"duplicate PLY element {active_name!r}")
            elements.append(
                PLYElement(active_name, active_count, tuple(active_properties))
            )
        active_name = None
        active_count = None
        active_properties = []

    for index, raw_line in enumerate(raw_lines[1:], start=2):
        try:
            line = raw_line.decode("ascii", errors="strict").rstrip("\r\n")
        except UnicodeDecodeError as exc:
            raise PLYHeaderError(f"header line {index} is not ASCII") from exc
        stripped = line.strip()
        if stripped == "":
            continue
        tokens = stripped.split()
        keyword = tokens[0]
        if keyword == "format":
            if format_seen or len(tokens) != 3:
                raise PLYHeaderError(f"malformed format declaration on header line {index}")
            format_seen = True
            format_name, version = tokens[1], tokens[2]
            if format_name != "binary_little_endian" or version != "1.0":
                raise PLYHeaderError(
                    "only format binary_little_endian 1.0 is supported"
                )
        elif keyword == "comment":
            comments.append(stripped)
        elif keyword == "element":
            if len(tokens) != 3:
                raise PLYHeaderError(f"malformed element declaration on header line {index}")
            finish_element()
            name = tokens[1]
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise PLYHeaderError(f"invalid element name {name!r}")
            try:
                count = int(tokens[2], 10)
            except ValueError as exc:
                raise PLYHeaderError(f"invalid element count {tokens[2]!r}") from exc
            if count < 0 or count > MAX_ELEMENT_COUNT:
                raise PLYHeaderError(f"invalid element count {count} for {name!r}")
            active_name, active_count = name, count
        elif keyword == "property":
            if active_name is None:
                raise PLYHeaderError(f"property appears before an element on line {index}")
            if len(tokens) == 3:
                type_name, name = tokens[1], tokens[2]
                if type_name not in SCALAR_DTYPES:
                    raise PLYHeaderError(
                        f"unsupported scalar type {type_name!r} on header line {index}"
                    )
                prop = PLYProperty(name=name, type_name=type_name)
            elif len(tokens) == 5 and tokens[1] == "list":
                count_type, item_type, name = tokens[2], tokens[3], tokens[4]
                if count_type not in SCALAR_DTYPES or item_type not in SCALAR_DTYPES:
                    raise PLYHeaderError(
                        f"unsupported list type on header line {index}: {' '.join(tokens)}"
                    )
                prop = PLYProperty(
                    name=name,
                    list_count_type=count_type,
                    list_item_type=item_type,
                )
            else:
                raise PLYHeaderError(f"malformed property declaration on line {index}")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", prop.name):
                raise PLYHeaderError(f"invalid property name {prop.name!r}")
            if any(existing.name == prop.name for existing in active_properties):
                raise PLYHeaderError(
                    f"duplicate property {prop.name!r} in element {active_name!r}"
                )
            active_properties.append(prop)
        elif keyword == "end_header":
            if len(tokens) != 1 or end_seen:
                raise PLYHeaderError(f"malformed end_header on line {index}")
            finish_element()
            end_seen = True
            if index != len(raw_lines):
                raise PLYHeaderError("data appears after end_header in header input")
        else:
            raise PLYHeaderError(f"unsupported PLY header directive {keyword!r} on line {index}")

    if not format_seen:
        raise PLYHeaderError("PLY header has no format declaration")
    if not end_seen:
        raise PLYHeaderError("PLY header has no end_header declaration")
    if not elements:
        raise PLYHeaderError("PLY header contains no elements")
    return PLYHeader(
        elements=tuple(elements),
        raw_bytes=blob,
        payload_offset=len(blob),
        format_name=format_name,
        version=version,
        comments=tuple(comments),
    )


def source_data(source: PLYSource) -> PLYSourceData:
    """Read only enough bytes to parse a source's PLY header."""

    return _read_header_blob(source)


def structured_dtype(element: PLYElement) -> np.dtype:
    """Build a packed NumPy dtype for an all-scalar PLY element."""

    if any(prop.is_list for prop in element.properties):
        raise PLYSchemaError(f"list properties are unsupported for element {element.name!r}")
    return np.dtype([(prop.name, prop.scalar_dtype) for prop in element.properties], align=False)


def expected_payload_bytes(header: PLYHeader) -> int:
    """Return the fixed-width payload length for an all-scalar header."""

    total = 0
    for element in header.elements:
        try:
            width = element.record_size
        except PLYSchemaError:
            raise
        total += element.count * width
    return total


def read_element_array(
    source_info: PLYSourceData,
    element: PLYElement,
    *,
    require_exact_payload: bool = True,
) -> np.ndarray:
    """Read one fixed-width element without constructing per-property copies."""

    dtype = structured_dtype(element)
    expected = element.count * int(dtype.itemsize)
    available = source_info.size_bytes - source_info.header.payload_offset
    if expected < 0 or available < expected:
        raise PLYPayloadError(
            f"truncated {element.name!r} payload: expected {expected} bytes, found {available}"
        )
    if require_exact_payload and available != expected:
        raise PLYPayloadError(
            f"payload length mismatch: expected {expected} bytes, found {available}"
        )

    start = source_info.header.payload_offset
    end = start + expected
    if source_info.in_memory is not None:
        return np.frombuffer(source_info.in_memory[start:end], dtype=dtype, count=element.count)
    assert source_info.path is not None
    with source_info.path.open("rb") as handle:
        handle.seek(start)
        array = np.fromfile(handle, dtype=dtype, count=element.count)
        if array.shape[0] != element.count:
            raise PLYPayloadError(
                f"truncated {element.name!r} payload while reading {source_info.path}"
            )
        if require_exact_payload and handle.read(1):
            raise PLYPayloadError(f"trailing bytes after {element.name!r} payload")
    return array


def read_raw_payload(source_info: PLYSourceData) -> bytes:
    """Read a source payload for callers that explicitly need raw bytes."""

    start = source_info.header.payload_offset
    if source_info.in_memory is not None:
        return source_info.in_memory[start:]
    assert source_info.path is not None
    with source_info.path.open("rb") as handle:
        handle.seek(start)
        return handle.read()


def render_header(elements: Sequence[PLYElement], *, comments: Iterable[str] = ()) -> bytes:
    """Render a new binary little-endian PLY header."""

    return PLYHeader(
        elements=tuple(elements),
        raw_bytes=b"",
        payload_offset=0,
        comments=tuple(comments),
    ).render()

