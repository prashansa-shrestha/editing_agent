"""Optional full-scene Milestone 1A integration gate."""

from __future__ import annotations

import shutil
from pathlib import Path
import subprocess

import numpy as np
import pytest

from scene_agent.scene import (
    CANONICAL_PROPERTY_NAMES,
    compare_canonical_ply,
    decode_compressed_ply,
    load_canonical_ply,
    sha256_file,
    validate_canonical_ply,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REAL_SCENE = Path("/data/Desktop/Prashansa/major/scenebench/data/InteriorGS/0231_840445/3dgs_compressed.ply")
EXPECTED_SHA256 = "c82a07ca1f2d4502df9dfb83e0b26973392e5139f78d3fe1879427c272b426da"
EXPECTED_COUNT = 524_508


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


def test_real_scene_decode_roundtrip_is_lossless_and_source_immutable(artifact_root: Path):
    if not REAL_SCENE.is_file():
        pytest.skip(f"optional real scene is absent: {REAL_SCENE}")
    if not _node_playcanvas_available():
        pytest.skip("Node.js or the pinned PlayCanvas dependency is unavailable")

    before = sha256_file(REAL_SCENE)
    assert before == EXPECTED_SHA256
    report = decode_compressed_ply(
        REAL_SCENE,
        artifact_root / "real-scene-decoded.ply",
        repository_root=REPOSITORY_ROOT,
    )
    assert report.source_gaussian_count == EXPECTED_COUNT
    assert report.source_sha256_before == EXPECTED_SHA256
    assert report.source_sha256_after == EXPECTED_SHA256
    assert report.returncode == 0
    assert report.peak_rss_bytes < 1 * 1024 * 1024 * 1024

    inspection = validate_canonical_ply(report.output_path)
    assert inspection.vertex_count == EXPECTED_COUNT
    assert inspection.property_names == CANONICAL_PROPERTY_NAMES
    assert len(inspection.property_names) == 59

    decoded = load_canonical_ply(report.output_path, reject_nonfinite=True)
    assert decoded.data.dtype.names == CANONICAL_PROPERTY_NAMES
    for name in CANONICAL_PROPERTY_NAMES:
        assert decoded.data[name].dtype == np.dtype("<f4")
        assert np.isfinite(decoded.data[name]).all()

    roundtrip_path = artifact_root / "real-scene-roundtrip.ply"
    decoded.save(roundtrip_path, repository_root=REPOSITORY_ROOT)
    comparison = compare_canonical_ply(report.output_path, roundtrip_path, exact=True)
    assert comparison.equal
    assert comparison.same_schema
    assert comparison.same_values
    assert comparison.same_header_bytes
    assert sha256_file(REAL_SCENE) == EXPECTED_SHA256

    # Keep the resource evidence visible in ordinary pytest output when this
    # optional gate is run with ``-s`` or in a CI log.
    target_status = "within-target" if not report.memory_report.above_target else "above-target"
    print(
        "real-scene decode status=passed "
        f"target_status={target_status} "
        f"runtime_seconds={report.runtime_seconds:.3f} "
        f"peak_rss_mib={report.peak_memory_mib:.1f} "
        f"gaussian_count={report.gaussian_count}"
    )
