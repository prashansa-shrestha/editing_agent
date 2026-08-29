"""Compressed PlayCanvas schema, decoder, and deterministic row-order tests."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
import subprocess

import numpy as np
import pytest

from scene_agent.scene import (
    CANONICAL_PROPERTY_NAMES,
    DecoderUnavailableError,
    OutputExistsError,
    PLYHeaderError,
    PLYPayloadError,
    PLYSchemaError,
    UnsafePathError,
    compare_canonical_ply,
    decode_compressed_ply,
    inspect_compressed_ply,
    load_canonical_ply,
    sha256_file,
    validate_compressed_ply,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DECODER_SCRIPT = REPOSITORY_ROOT / "scripts" / "decode_compressed_ply.mjs"


def _node_playcanvas_available() -> bool:
    node = shutil.which("node")
    if node is None:
        return False
    probe = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            "import 'playcanvas/build/playcanvas/src/scene/gsplat/gsplat-compressed-data.js'",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return probe.returncode == 0


def _require_node_playcanvas() -> str:
    if not _node_playcanvas_available():
        pytest.skip("Node.js or the pinned PlayCanvas dependency is unavailable")
    return shutil.which("node") or "node"


def test_valid_compressed_inspection_and_alias(compressed_factory):
    source = compressed_factory(vertex_count=4)

    inspection = inspect_compressed_ply(source)
    alias = validate_compressed_ply(source)
    assert inspection == alias
    assert inspection.chunk_count == 1
    assert inspection.vertex_count == 4
    assert inspection.sh_count == 4
    assert inspection.chunk_size == 256
    assert inspection.chunk_record_size == 18 * 4
    assert inspection.vertex_record_size == 4 * 4
    assert inspection.sh_record_size == 45
    assert inspection.actual_payload_bytes == inspection.expected_payload_bytes
    assert inspection.payload_length == inspection.expected_payload_bytes


def test_malformed_compressed_header_is_rejected(compressed_factory, artifact_root: Path):
    source = artifact_root / "compressed-malformed-header.ply"
    source.write_bytes(b"ply\nformat binary_little_endian 1.0\n")

    with pytest.raises(PLYHeaderError, match="end_header|header"):
        inspect_compressed_ply(source)


@pytest.mark.parametrize(
    ("variant", "error_type", "message"),
    [
        ("truncated", PLYPayloadError, "truncated"),
        ("trailing", PLYPayloadError, "trailing"),
        ("list", PLYSchemaError, "list"),
        ("unknown", PLYSchemaError, "property order|packed_position"),
    ],
)
def test_malformed_compressed_payload_and_schema_are_rejected(
    compressed_factory,
    variant: str,
    error_type: type[Exception],
    message: str,
):
    source = compressed_factory(vertex_count=4, variant=variant if variant in {"list", "unknown"} else None)
    raw = source.read_bytes()
    if variant == "truncated":
        source.write_bytes(raw[:-1])
    elif variant == "trailing":
        source.write_bytes(raw + b"\x00")

    with pytest.raises(error_type, match=message):
        inspect_compressed_ply(source)


def test_decode_output_has_frozen_canonical_schema_finite_values_and_stable_ids(
    compressed_factory, artifact_root: Path
):
    _require_node_playcanvas()
    source = compressed_factory(vertex_count=4)
    before = sha256_file(source)
    report = decode_compressed_ply(
        source,
        artifact_root / "decoded-valid.ply",
        repository_root=REPOSITORY_ROOT,
    )

    assert report.returncode == 0
    assert report.source_gaussian_count == 4
    assert report.output_path.is_file()
    assert report.output_path != source.resolve()
    assert report.partial_output_path.name.startswith(
        f".{report.output_path.name}.partial-"
    )
    assert not report.partial_output_path.exists()
    assert report.source_sha256_before == before
    assert report.source_sha256_after == before
    assert report.elapsed_seconds > 0
    assert report.peak_rss_bytes >= 0

    inspection = validate_compressed_ply(source)
    canonical = load_canonical_ply(report.output_path, reject_nonfinite=True)
    canonical_info = canonical.canonical if hasattr(canonical, "canonical") else canonical
    assert inspection.vertex_count == canonical.gaussian_count
    assert canonical_info.property_names == CANONICAL_PROPERTY_NAMES
    assert len(canonical_info.property_names) == 59
    assert canonical_info.data.dtype.names == CANONICAL_PROPERTY_NAMES
    for name in CANONICAL_PROPERTY_NAMES:
        assert canonical_info.data[name].dtype == np.dtype("<f4")
        assert np.isfinite(canonical_info.data[name]).all()
    assert np.array_equal(canonical_info.gaussian_ids, np.arange(4, dtype=np.int64))
    assert sha256_file(source) == before


def test_decoder_preserves_compressed_row_order_and_opacity_endpoints(
    compressed_factory, artifact_root: Path
):
    _require_node_playcanvas()
    source = compressed_factory(vertex_count=4)
    report = decode_compressed_ply(
        source,
        artifact_root / "decoded-row-order.ply",
        repository_root=REPOSITORY_ROOT,
    )
    scene = load_canonical_ply(report.output_path, reject_nonfinite=True)

    # The fixture encodes these rows in strictly increasing x order.  The
    # expected values use the same normalized 11-bit mapping as PlayCanvas;
    # checking every row catches sort-by-position and chunk-order mistakes.
    expected_x = np.asarray(
        [10.0 * (101 + row * 503) / 2047.0 for row in range(4)],
        dtype=np.float32,
    )
    np.testing.assert_allclose(scene.column("x"), expected_x, rtol=0, atol=2e-6)
    assert np.all(np.diff(scene.column("x")) > 0)
    np.testing.assert_array_equal(scene.gaussian_ids, np.arange(4, dtype=np.int64))
    assert scene.column("opacity")[0] == np.float32(-40.0)
    assert scene.column("opacity")[1] == np.float32(40.0)
    assert np.isfinite(scene.column("opacity")).all()


def test_repeated_decoder_runs_are_byte_and_value_deterministic(compressed_factory, artifact_root: Path):
    _require_node_playcanvas()
    source = compressed_factory(vertex_count=6)
    first = decode_compressed_ply(
        source,
        artifact_root / "decoded-repeat-a.ply",
        repository_root=REPOSITORY_ROOT,
    )
    second = decode_compressed_ply(
        source,
        artifact_root / "decoded-repeat-b.ply",
        repository_root=REPOSITORY_ROOT,
    )

    assert first.source_sha256_before == second.source_sha256_before == sha256_file(source)
    assert first.source_sha256_after == second.source_sha256_after
    assert first.output_path.read_bytes() == second.output_path.read_bytes()
    comparison = compare_canonical_ply(first.output_path, second.output_path, exact=True)
    assert comparison.equal
    assert comparison.same_header_bytes


def test_decoder_refuses_existing_final_and_ignores_unowned_legacy_partial(
    compressed_factory, artifact_root: Path
):
    _require_node_playcanvas()
    source = compressed_factory(vertex_count=3)
    output = artifact_root / "decoded-overwrite.ply"
    output.write_bytes(b"caller-owned")
    with pytest.raises(OutputExistsError, match="overwrite|existing"):
        decode_compressed_ply(source, output, repository_root=REPOSITORY_ROOT)
    assert output.read_bytes() == b"caller-owned"

    partial_output = artifact_root / "decoded-partial.ply"
    partial = partial_output.with_name(f".{partial_output.name}.partial")
    partial.write_bytes(b"caller-owned-partial")
    report = decode_compressed_ply(source, partial_output, repository_root=REPOSITORY_ROOT)
    assert report.output_path == partial_output
    assert partial.read_bytes() == b"caller-owned-partial"


def test_decoder_rejects_source_as_output_and_keeps_source_unchanged(compressed_factory):
    _require_node_playcanvas()
    source = compressed_factory(vertex_count=3)
    before = source.read_bytes()
    with pytest.raises(OutputExistsError, match="overwrite|output|existing"):
        decode_compressed_ply(source, source, repository_root=REPOSITORY_ROOT)
    assert source.read_bytes() == before


def test_decoder_unavailable_message_names_requested_node_executable(compressed_factory, artifact_root: Path):
    source = compressed_factory(vertex_count=2)
    missing_node = "node-executable-that-is-not-installed-for-milestone1a"
    with pytest.raises(DecoderUnavailableError, match="Node.js|node-executable") as raised:
        decode_compressed_ply(
            source,
            artifact_root / "decoded-unavailable.ply",
            repository_root=REPOSITORY_ROOT,
            node_executable=missing_node,
        )
    assert missing_node in str(raised.value)


def test_decoder_unavailable_message_names_missing_frozen_script(compressed_factory, artifact_root: Path):
    if shutil.which("node") is None:
        pytest.skip("Node.js is unavailable; missing-script validation is not reachable")
    source = compressed_factory(vertex_count=2)
    missing_script = artifact_root / "missing-decoder-script.mjs"
    with pytest.raises(DecoderUnavailableError, match="not found|decoder script") as raised:
        decode_compressed_ply(
            source,
            artifact_root / "decoded-missing-script.ply",
            repository_root=REPOSITORY_ROOT,
            decoder_script=missing_script,
        )
    assert str(missing_script) in str(raised.value)


def test_node_cli_help_and_output_contract(compressed_factory, artifact_root: Path):
    node = _require_node_playcanvas()
    source = compressed_factory(vertex_count=2)
    help_result = subprocess.run(
        [node, str(DECODER_SCRIPT), "--help"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_result.returncode == 0
    assert "Usage: node scripts/decode_compressed_ply.mjs" in help_result.stdout

    output = artifact_root / "decoded-cli.ply"
    output_fd = os.open(output, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        result = subprocess.run(
            [node, str(DECODER_SCRIPT), str(source), str(output_fd)],
            cwd=REPOSITORY_ROOT,
            capture_output=True,
            text=True,
            check=False,
            pass_fds=(output_fd,),
        )
    finally:
        os.close(output_fd)
    assert result.returncode == 0, result.stderr
    assert "Decoded 2 Gaussian rows" in result.stdout
    assert output.is_file()
    assert load_canonical_ply(output, reject_nonfinite=True).gaussian_count == 2


def test_parent_swap_before_node_write_cannot_escape_or_publish(
    compressed_factory, artifact_root: Path, tmp_path: Path, monkeypatch
):
    """The inherited fd remains anchored even after the visible parent is swapped."""

    _require_node_playcanvas()
    import scene_agent.scene.decoder as decoder_module

    source = compressed_factory(vertex_count=3)
    parent = artifact_root / "parent-swap"
    parent.mkdir()
    moved_parent = artifact_root / "parent-swap-retained"
    escape = tmp_path / "escape"
    escape.mkdir()
    output = parent / "decoded.ply"
    original_runner = decoder_module._run_node_process

    def swap_then_run(command, *, timeout_seconds, output_fd):
        parent.rename(moved_parent)
        parent.symlink_to(escape, target_is_directory=True)
        return original_runner(
            command,
            timeout_seconds=timeout_seconds,
            output_fd=output_fd,
        )

    monkeypatch.setattr(decoder_module, "_run_node_process", swap_then_run)
    try:
        with pytest.raises(UnsafePathError, match="parent changed"):
            decode_compressed_ply(source, output, repository_root=REPOSITORY_ROOT)
        assert not (escape / output.name).exists()
        assert not (moved_parent / output.name).exists()
        assert not list(moved_parent.glob(f".{output.name}.partial-*"))
    finally:
        if parent.is_symlink():
            parent.unlink()
        if moved_parent.exists():
            moved_parent.rename(parent)


def test_partial_replacement_is_not_published_or_deleted(
    compressed_factory, artifact_root: Path, monkeypatch
):
    """Replacing the partial name cannot substitute or erase a foreign inode."""

    _require_node_playcanvas()
    import scene_agent.scene.decoder as decoder_module
    import scene_agent.scene.paths as paths_module

    source = compressed_factory(vertex_count=3)
    output = artifact_root / "partial-replaced.ply"
    captured = {}
    original_create = paths_module.SecureOutputTarget.create_partial
    original_runner = decoder_module._run_node_process

    def capture_partial(target):
        partial = original_create(target)
        captured["partial"] = partial
        return partial

    def replace_after_node(command, *, timeout_seconds, output_fd):
        result = original_runner(
            command,
            timeout_seconds=timeout_seconds,
            output_fd=output_fd,
        )
        partial = captured["partial"]
        partial.path.unlink()
        partial.path.write_bytes(b"foreign-partial")
        return result

    monkeypatch.setattr(paths_module.SecureOutputTarget, "create_partial", capture_partial)
    monkeypatch.setattr(decoder_module, "_run_node_process", replace_after_node)

    with pytest.raises(UnsafePathError, match="publication|linkat"):
        decode_compressed_ply(source, output, repository_root=REPOSITORY_ROOT)
    foreign = captured["partial"].path
    assert foreign.read_bytes() == b"foreign-partial"
    assert not output.exists()


def test_concurrent_partial_name_collision_preserves_foreign_file(
    compressed_factory, artifact_root: Path, monkeypatch
):
    """O_EXCL retries a unique name and cleanup never removes the collision."""

    _require_node_playcanvas()
    import scene_agent.scene.paths as paths_module

    source = compressed_factory(vertex_count=3)
    output = artifact_root / "partial-collision.ply"
    collision = output.with_name(f".{output.name}.partial-collision")
    collision.write_bytes(b"foreign-collision")
    tokens = iter(("collision", "owned-unique"))
    monkeypatch.setattr(paths_module.secrets, "token_hex", lambda _size: next(tokens))

    report = decode_compressed_ply(source, output, repository_root=REPOSITORY_ROOT)

    assert report.output_path == output
    assert collision.read_bytes() == b"foreign-collision"
    assert not report.partial_output_path.exists()
