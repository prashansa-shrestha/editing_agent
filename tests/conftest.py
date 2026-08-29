"""Small runtime-generated fixtures for the deterministic Milestone 1A tests.

The fixtures intentionally live beneath ``outputs/milestone1`` because the
decoder's output policy applies to every generated scene artifact, including
test inputs.  A private per-session directory keeps cleanup isolated from any
artifacts a caller may already have in the repository output directory.
"""

from __future__ import annotations

from pathlib import Path
import struct
import uuid

import numpy as np
import pytest

from scene_agent.scene import CANONICAL_PROPERTY_NAMES


CHUNK_PROPERTIES = (
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
PACKED_VERTEX_PROPERTIES = (
    "packed_position",
    "packed_rotation",
    "packed_scale",
    "packed_color",
)
SH_PROPERTIES = tuple(f"f_rest_{index}" for index in range(45))


def _compressed_header(vertex_count: int, *, variant: str | None = None) -> bytes:
    chunk_count = (vertex_count + 255) // 256
    lines = [
        "ply",
        "format binary_little_endian 1.0",
        f"element chunk {chunk_count}",
        *(f"property float {name}" for name in CHUNK_PROPERTIES),
        f"element vertex {vertex_count}",
        *(f"property uint {name}" for name in PACKED_VERTEX_PROPERTIES),
        f"element sh {vertex_count}",
        *(f"property uchar {name}" for name in SH_PROPERTIES),
        "end_header",
    ]
    header = ("\n".join(lines) + "\n").encode("ascii")
    if variant == "unknown":
        header = header.replace(
            b"property uint packed_position",
            b"property uint packed_position_unknown",
            1,
        )
    elif variant == "list":
        header = header.replace(
            b"property uint packed_position",
            b"property list uchar uint packed_position",
            1,
        )
    return header


def pack_111011(x: int, y: int, z: int) -> int:
    """Pack PlayCanvas' 11/10/11-bit normalized integer layout."""

    if not (0 <= x <= 2047 and 0 <= y <= 1023 and 0 <= z <= 2047):
        raise ValueError("packed 11/10/11 components are out of range")
    return (x << 21) | (y << 11) | z


def pack_rotation() -> int:
    """A near-identity compressed quaternion with a finite decompression."""

    # The three encoded components are centered around 0.5.  The two most
    # significant bits select the largest quaternion component (w).
    return (512 << 20) | (512 << 10) | 512


def pack_color(r: int, g: int, b: int, alpha: int) -> int:
    if not all(0 <= value <= 255 for value in (r, g, b, alpha)):
        raise ValueError("packed RGBA components are out of range")
    return (r << 24) | (g << 16) | (b << 8) | alpha


def _compressed_payload(vertex_count: int) -> bytes:
    chunk_count = (vertex_count + 255) // 256
    chunks = bytearray()
    # Position and color bounds are deliberately non-degenerate.  Log-scale
    # bounds are finite and leave enough room for every synthetic row.
    chunk = (0.0, 0.0, 0.0, 10.0, 10.0, 10.0, -1.0, -1.0, -1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
    for _ in range(chunk_count):
        chunks.extend(struct.pack("<18f", *chunk))

    vertices = bytearray()
    # Distinct rows make accidental sorting/reordering observable.  End-point
    # alpha values also exercise the decoder's finite -40/+40 opacity policy.
    alpha_values = (0, 255, 128, 64, 32, 192, 16, 224)
    for row in range(vertex_count):
        x = min(2047, 101 + row * 503)
        y = min(1023, 97 + row * 211)
        z = min(2047, 53 + row * 389)
        packed_position = pack_111011(x, y, z)
        packed_scale = pack_111011(100 + row * 13, 500 + row * 7, 1800 - row * 11)
        packed = (
            packed_position,
            pack_rotation(),
            packed_scale,
            pack_color(16 + row * 17, 32 + row * 13, 48 + row * 11, alpha_values[row % len(alpha_values)]),
        )
        vertices.extend(struct.pack("<4I", *packed))

    sh = bytearray()
    for row in range(vertex_count):
        sh.extend((row * 45 + index) % 256 for index in range(45))
    return bytes(chunks + vertices + sh)


def write_compressed_fixture(path: Path, vertex_count: int = 4, *, variant: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_compressed_header(vertex_count, variant=variant) + _compressed_payload(vertex_count))
    return path


def _canonical_specs(*, unknown: tuple[str, str] | None = None, variant: str | None = None) -> list[tuple[str, str]]:
    specs = [("float", name) for name in CANONICAL_PROPERTY_NAMES]
    if unknown is not None:
        # Inserting an unknown scalar between required fields verifies that
        # preservation does not depend on appending it at the end.
        specs.insert(10, unknown)
    if variant == "missing":
        specs = [spec for spec in specs if spec[1] != "rot_3"]
    elif variant == "reordered":
        first = specs.index(("float", "x"))
        second = specs.index(("float", "y"))
        specs[first], specs[second] = specs[second], specs[first]
    elif variant == "wrong_type":
        index = specs.index(("float", "x"))
        specs[index] = ("double", "x")
    elif variant == "list":
        specs.append(("list uchar float", "unsupported_list"))
    return specs


def _canonical_header(vertex_count: int, specs: list[tuple[str, str]]) -> bytes:
    lines = [
        "ply",
        "format binary_little_endian 1.0",
        "comment synthetic Milestone 1A canonical fixture",
        f"element vertex {vertex_count}",
        *(f"property {type_name} {name}" for type_name, name in specs),
        "end_header",
    ]
    return ("\n".join(lines) + "\n").encode("ascii")


def _canonical_dtype(type_name: str) -> np.dtype:
    if type_name in {"float", "float32"}:
        return np.dtype("<f4")
    if type_name in {"double", "float64"}:
        return np.dtype("<f8")
    if type_name in {"int", "int32"}:
        return np.dtype("<i4")
    raise ValueError(f"fixture does not support scalar type {type_name!r}")


def write_canonical_fixture(
    path: Path,
    vertex_count: int = 5,
    *,
    unknown: tuple[str, str] | None = None,
    variant: str | None = None,
    nonfinite: bool = False,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    specs = _canonical_specs(unknown=unknown, variant=variant)
    header = _canonical_header(vertex_count, specs)
    # A list-property schema is rejected before payload decoding, so a header
    # alone is enough and avoids pretending to encode variable-length records.
    if variant == "list":
        path.write_bytes(header)
        return path

    dtype = np.dtype([(name, _canonical_dtype(type_name)) for type_name, name in specs])
    rows = np.zeros(vertex_count, dtype=dtype)
    for index, (type_name, name) in enumerate(specs):
        field = rows[name]
        if name == "x":
            values = np.arange(vertex_count, dtype=np.float32) + 0.25
            if nonfinite and vertex_count > 1:
                values[1] = np.nan
        elif name == "y":
            values = np.arange(vertex_count, dtype=np.float32) + 10.25
        elif name == "z":
            values = np.arange(vertex_count, dtype=np.float32) + 20.25
        elif name.startswith("f_rest_"):
            values = np.full(vertex_count, index / 100.0, dtype=np.float32)
        elif name == "opacity":
            values = np.linspace(-2.0, 2.0, vertex_count, dtype=np.float32)
        elif name.startswith("rot_"):
            values = np.zeros(vertex_count, dtype=np.float32)
            if name == "rot_0":
                values.fill(1.0)
        elif name.startswith("scale_"):
            values = np.full(vertex_count, -0.5, dtype=np.float32)
        elif name.startswith("f_dc_"):
            values = np.full(vertex_count, index / 10.0, dtype=np.float32)
        elif name == "quality":
            values = np.linspace(1.5, 2.5, vertex_count, dtype=np.float64)
        elif name == "label_id":
            values = np.arange(vertex_count, dtype=np.int32) + 100
        else:
            values = np.arange(vertex_count, dtype=np.float32) + index
        field[:] = np.asarray(values, dtype=field.dtype)

    with path.open("wb") as handle:
        handle.write(header)
        rows.tofile(handle)
    return path


@pytest.fixture(scope="session")
def artifact_root() -> Path:
    """Own one isolated output subtree and remove it after the test session."""

    repository_root = Path(__file__).resolve().parents[1]
    milestone_root = repository_root / "outputs" / "milestone1"
    milestone_root.mkdir(parents=True, exist_ok=True)
    owned = milestone_root / f".pytest-milestone1-{uuid.uuid4().hex}"
    owned.mkdir()
    try:
        yield owned
    finally:
        # The directory is uniquely named by this session, so recursive cleanup
        # cannot affect a caller-owned artifact.
        import shutil

        shutil.rmtree(owned, ignore_errors=True)


@pytest.fixture
def compressed_factory(artifact_root: Path):
    def make(vertex_count: int = 4, *, variant: str | None = None) -> Path:
        path = artifact_root / f"compressed-{uuid.uuid4().hex}.ply"
        return write_compressed_fixture(path, vertex_count, variant=variant)

    return make


@pytest.fixture
def canonical_factory(artifact_root: Path):
    def make(
        vertex_count: int = 5,
        *,
        unknown: tuple[str, str] | None = None,
        variant: str | None = None,
        nonfinite: bool = False,
    ) -> Path:
        path = artifact_root / f"canonical-{uuid.uuid4().hex}.ply"
        return write_canonical_fixture(
            path,
            vertex_count,
            unknown=unknown,
            variant=variant,
            nonfinite=nonfinite,
        )

    return make

