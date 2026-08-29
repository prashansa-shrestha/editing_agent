"""High-level GaussianScene wrapper for Milestone 1 canonical data."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Union

from .canonical import CanonicalScene, load_canonical_ply, write_canonical_ply
from .compressed import inspect_compressed_ply
from .decoder import DecodeReport, decode_compressed_ply
from .fingerprint import sha256_file
from .errors import PLYError


PathLike = Union[str, Path]


@dataclass
class GaussianScene:
    """A loaded canonical Gaussian table and its provenance metadata."""

    canonical: CanonicalScene
    source_path: Path | None = None
    source_sha256: str | None = None
    coordinate_system: str = "unknown"
    decode_report: DecodeReport | None = None

    def __post_init__(self) -> None:
        if self.source_path is None:
            self.source_path = self.canonical.source_path
        if self.source_sha256 is None and self.source_path is not None:
            # Do not force hashing in CanonicalScene itself; this high-level
            # object is explicitly provenance-aware.
            self.source_sha256 = sha256_file(self.source_path)
        if self.coordinate_system == "unknown":
            self.coordinate_system = self.canonical.coordinate_system

    @classmethod
    def load(
        cls,
        path: PathLike,
        *,
        coordinate_system: str = "unknown",
        decode_output: PathLike | None = None,
        repository_root: PathLike | None = None,
        node_executable: str = "node",
        decoder_script: PathLike | None = None,
    ) -> "GaussianScene":
        """Load canonical PLY, or safely decode a compressed PLY first."""

        source = Path(path).expanduser().resolve()
        try:
            # Header-only inspection is cheap and avoids suffix-based guesses.
            compressed = inspect_compressed_ply(source)
        except PLYError:
            compressed = None
        report: DecodeReport | None = None
        loaded_path = source
        if compressed is not None:
            report = decode_compressed_ply(
                source,
                decode_output,
                repository_root=repository_root,
                node_executable=node_executable,
                decoder_script=decoder_script,
            )
            loaded_path = report.output_path
        canonical = load_canonical_ply(
            loaded_path,
            coordinate_system=coordinate_system,
        )
        return cls(
            canonical=canonical,
            source_path=source,
            source_sha256=sha256_file(source),
            coordinate_system=coordinate_system,
            decode_report=report,
        )

    @classmethod
    def from_canonical(
        cls,
        canonical: CanonicalScene,
        *,
        coordinate_system: str = "unknown",
    ) -> "GaussianScene":
        return cls(canonical=canonical, coordinate_system=coordinate_system)

    def clone(self) -> "GaussianScene":
        return GaussianScene(
            canonical=self.canonical.clone(),
            source_path=None,
            source_sha256=self.source_sha256,
            coordinate_system=self.coordinate_system,
            decode_report=self.decode_report,
        )

    @property
    def data(self):
        return self.canonical.data

    @property
    def positions(self):
        return self.canonical.positions

    @property
    def f_dc(self):
        return self.canonical.f_dc

    @property
    def f_rest(self):
        return self.canonical.f_rest

    @property
    def opacity(self):
        return self.canonical.opacity

    @property
    def scales(self):
        return self.canonical.scales

    @property
    def rotations(self):
        return self.canonical.rotations

    @property
    def gaussian_ids(self):
        return self.canonical.gaussian_ids

    def gaussian_count(self) -> int:
        return self.canonical.gaussian_count

    @property
    def property_digest(self) -> dict[str, str]:
        return self.canonical.property_digest

    def save(
        self,
        path: PathLike,
        *,
        repository_root: PathLike | None = None,
        refuse_existing: bool = True,
    ) -> Path:
        """Save to a new path; the original source remains untouched."""

        return write_canonical_ply(
            self.canonical,
            path,
            repository_root=repository_root,
            refuse_existing=refuse_existing,
        )


def load_scene(path: PathLike, **kwargs) -> GaussianScene:
    return GaussianScene.load(path, **kwargs)


read_scene = load_scene
