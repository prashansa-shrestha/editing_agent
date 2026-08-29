"""Streaming file fingerprints used by the immutable-source workflow."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import BinaryIO, Union


PathLike = Union[str, Path]
DEFAULT_HASH_CHUNK_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class FileFingerprint:
    """Stable content fingerprint and size for one regular file."""

    sha256: str
    size_bytes: int

    @property
    def digest(self) -> str:
        """Alias used by callers that call a hash a digest."""

        return self.sha256

    def __str__(self) -> str:
        return self.sha256


def _as_path(path: PathLike) -> Path:
    return Path(path).expanduser()


def sha256_file(path: PathLike, *, chunk_size: int = DEFAULT_HASH_CHUNK_BYTES) -> str:
    """Return the SHA-256 hex digest of ``path`` without loading it wholly.

    The function deliberately opens the resolved file for reading only.  It
    never creates parent directories and never writes to the source scene.
    """

    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    source = _as_path(path)
    if not source.is_file():
        raise FileNotFoundError(f"source file does not exist or is not a regular file: {source}")
    digest = hashlib.sha256()
    with source.open("rb") as handle:
        while True:
            block = handle.read(chunk_size)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def fingerprint_file(path: PathLike, *, chunk_size: int = DEFAULT_HASH_CHUNK_BYTES) -> FileFingerprint:
    """Return a content hash and byte size for ``path``."""

    source = _as_path(path)
    digest = sha256_file(source, chunk_size=chunk_size)
    return FileFingerprint(sha256=digest, size_bytes=source.stat().st_size)


sha256_path = sha256_file

