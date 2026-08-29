"""Deterministic canonical PLY validation and round-trip tests."""

from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np
import pytest

from scene_agent.scene import (
    CANONICAL_PROPERTY_NAMES,
    OutputExistsError,
    PLYPayloadError,
    PLYSchemaError,
    compare_canonical_ply,
    fingerprint_file,
    load_canonical_ply,
    resolve_safe_output_path,
    validate_canonical_ply,
    write_canonical_ply,
)
from scene_agent.scene.errors import UnsafePathError


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_canonical_schema_count_finiteness_order_and_float32_types(canonical_factory):
    source = canonical_factory(vertex_count=7)

    inspection = validate_canonical_ply(source)
    assert inspection.gaussian_count == 7
    assert inspection.property_names == CANONICAL_PROPERTY_NAMES
    assert len(inspection.property_names) == 59
    assert inspection.unknown_property_names == ()
    assert inspection.record_size == 59 * 4

    scene = load_canonical_ply(source, coordinate_system="Z-up", reject_nonfinite=True)
    assert scene.gaussian_count == 7
    assert scene.coordinate_system == "Z-up"
    assert scene.data.dtype.names == CANONICAL_PROPERTY_NAMES
    for name in CANONICAL_PROPERTY_NAMES:
        assert scene.data[name].dtype == np.dtype("<f4")
        assert np.isfinite(scene.data[name]).all()
    assert np.array_equal(scene.gaussian_ids, np.arange(7, dtype=np.int64))
    # Stable IDs are row indices, not a value-derived or sorted ordering.
    np.testing.assert_array_equal(scene.column("x"), np.arange(7, dtype=np.float32) + 0.25)


def test_canonical_noop_write_reload_is_exact_and_preserves_header(canonical_factory, artifact_root: Path):
    source = canonical_factory(vertex_count=6)
    loaded = load_canonical_ply(source, reject_nonfinite=True)
    output = artifact_root / "canonical-noop.ply"

    written = write_canonical_ply(loaded, output, repository_root=REPOSITORY_ROOT)
    reloaded = load_canonical_ply(written, reject_nonfinite=True)
    comparison = compare_canonical_ply(source, reloaded, exact=True)

    assert written == output
    assert comparison.equal
    assert comparison.exact
    assert comparison.same_schema
    assert comparison.same_values
    assert comparison.same_header_bytes
    assert loaded.property_digest == reloaded.property_digest


def test_invalid_canonical_partial_never_becomes_final(canonical_factory, artifact_root: Path, monkeypatch):
    """A failed temporary-file validation must not publish or retain output."""

    import scene_agent.scene.canonical as canonical_module

    source = canonical_factory(vertex_count=4)
    loaded = load_canonical_ply(source, reject_nonfinite=True)
    output = artifact_root / "canonical-invalid-partial.ply"

    def reject_partial(*_args, **_kwargs):
        raise PLYSchemaError("injected invalid canonical partial")

    monkeypatch.setattr(canonical_module, "_validate_written_partial", reject_partial)
    with pytest.raises(PLYSchemaError, match="partial"):
        write_canonical_ply(loaded, output, repository_root=REPOSITORY_ROOT)

    assert not output.exists()
    assert not list(output.parent.glob(f".{output.name}.partial-*"))


def test_canonical_partial_name_replacement_cannot_change_published_inode(
    canonical_factory, artifact_root: Path, monkeypatch
):
    """Validation/publication stay bound to the retained fd, not its old name."""

    import scene_agent.scene.canonical as canonical_module

    source = canonical_factory(vertex_count=4)
    loaded = load_canonical_ply(source, reject_nonfinite=True)
    output = artifact_root / "canonical-partial-replaced.ply"
    original_validate = canonical_module._validate_written_partial
    captured = {}

    def validate_then_replace(scene, partial):
        original_validate(scene, partial)
        retained_name = partial.path.with_name(partial.path.name + ".retained")
        partial.path.rename(retained_name)
        partial.path.write_bytes(b"foreign-canonical-partial")
        captured["foreign"] = partial.path
        captured["retained"] = retained_name

    monkeypatch.setattr(canonical_module, "_validate_written_partial", validate_then_replace)
    written = write_canonical_ply(loaded, output, repository_root=REPOSITORY_ROOT)

    assert compare_canonical_ply(source, written, exact=True).equal
    assert captured["foreign"].read_bytes() == b"foreign-canonical-partial"
    assert compare_canonical_ply(captured["retained"], written, exact=True).equal


def test_public_output_apis_have_no_output_root_override():
    import scene_agent.scene.canonical as canonical_module
    import scene_agent.scene.decoder as decoder_module
    import scene_agent.scene.paths as paths_module

    assert "output_root" not in inspect.signature(canonical_module.write_canonical_ply).parameters
    assert "output_root" not in inspect.signature(decoder_module.decode_compressed_ply).parameters
    assert "output_root" not in inspect.signature(paths_module.resolve_safe_output_path).parameters


def test_unknown_scalar_property_survives_load_write_reload_and_compare(canonical_factory, artifact_root: Path):
    source = canonical_factory(vertex_count=5, unknown=("double", "quality"))
    loaded = load_canonical_ply(source, reject_nonfinite=True)
    output = artifact_root / "canonical-unknown-roundtrip.ply"

    write_canonical_ply(loaded, output, repository_root=REPOSITORY_ROOT)
    reloaded = load_canonical_ply(output, reject_nonfinite=True)
    comparison = compare_canonical_ply(source, output, exact=True)

    assert comparison.equal
    expected_names = CANONICAL_PROPERTY_NAMES[:10] + ("quality",) + CANONICAL_PROPERTY_NAMES[10:]
    assert reloaded.property_names == expected_names
    assert reloaded.column("quality").dtype == np.dtype("<f8")
    np.testing.assert_array_equal(reloaded.column("quality"), np.linspace(1.5, 2.5, 5))
    assert reloaded.property_digest == loaded.property_digest


def test_nonfinite_canonical_values_are_rejected_when_requested(canonical_factory):
    source = canonical_factory(vertex_count=4, nonfinite=True)

    # Schema and payload sizing remain valid; finiteness is an explicit load
    # policy so callers can choose strict validation at the trust boundary.
    validate_canonical_ply(source)
    with pytest.raises(PLYSchemaError, match="non-finite|NaN|infinity"):
        load_canonical_ply(source, reject_nonfinite=True)


@pytest.mark.parametrize("variant", ["missing", "reordered", "wrong_type"])
def test_invalid_canonical_required_schema_is_rejected(canonical_factory, variant: str):
    source = canonical_factory(vertex_count=3, variant=variant)
    with pytest.raises(PLYSchemaError):
        validate_canonical_ply(source)


def test_unsupported_canonical_list_property_is_rejected(canonical_factory):
    source = canonical_factory(vertex_count=3, variant="list")
    with pytest.raises(PLYSchemaError, match="list"):
        validate_canonical_ply(source)


def test_canonical_payload_truncation_and_trailing_bytes_are_rejected(canonical_factory):
    source = canonical_factory(vertex_count=4)
    raw = source.read_bytes()

    truncated = source.with_name("canonical-truncated.ply")
    truncated.write_bytes(raw[:-1])
    with pytest.raises(PLYPayloadError, match="truncated"):
        validate_canonical_ply(truncated)

    trailing = source.with_name("canonical-trailing.ply")
    trailing.write_bytes(raw + b"\x00")
    with pytest.raises(PLYPayloadError, match="trailing"):
        validate_canonical_ply(trailing)


def test_source_fingerprint_and_output_policy_keep_source_immutable(canonical_factory, artifact_root: Path):
    source = canonical_factory(vertex_count=4)
    before = fingerprint_file(source)
    loaded = load_canonical_ply(source)
    output = artifact_root / "source-separated-copy.ply"

    write_canonical_ply(loaded, output, repository_root=REPOSITORY_ROOT)
    after = fingerprint_file(source)
    assert after == before
    assert output.resolve() != source.resolve()

    with pytest.raises(OutputExistsError):
        write_canonical_ply(loaded, output, repository_root=REPOSITORY_ROOT)


def test_safe_output_path_rejects_escape_and_source_directory(artifact_root: Path):
    repository_root = REPOSITORY_ROOT
    with pytest.raises(UnsafePathError):
        resolve_safe_output_path("../outside.ply", repository_root=repository_root)
    with pytest.raises(UnsafePathError):
        resolve_safe_output_path(repository_root / "outside.ply", repository_root=repository_root)


def test_safe_output_path_rejects_nested_symlink_parent_escape(artifact_root: Path):
    link = artifact_root / "nested-escape"
    try:
        link.symlink_to(Path("/var"), target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable in this test environment: {exc}")

    with pytest.raises(UnsafePathError, match="symlink|escape|output"):
        resolve_safe_output_path(
            link / "generated.ply",
            repository_root=REPOSITORY_ROOT,
        )
