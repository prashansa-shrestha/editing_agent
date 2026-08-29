"""Descriptor-anchored path policy for generated Milestone 1 artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
import errno
import os
from pathlib import Path
import secrets
import stat
from typing import Union

from .errors import OutputExistsError, UnsafePathError


PathLike = Union[str, Path]
MILESTONE_OUTPUT_RELATIVE = Path("outputs") / "milestone1"
_AT_EMPTY_PATH = 0x1000
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
_PARTIAL_FLAGS = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW


def find_repository_root(start: PathLike | None = None) -> Path:
    """Find the repository root by locating ``SPEC.md`` and ``AGENTS.md``."""

    candidate = Path(start).expanduser() if start is not None else Path.cwd()
    if candidate.is_file():
        candidate = candidate.parent
    candidate = candidate.resolve()
    for parent in (candidate, *candidate.parents):
        if (parent / "SPEC.md").is_file() and (parent / "AGENTS.md").is_file():
            return parent
    return candidate


def milestone_output_root(repository_root: PathLike | None = None) -> Path:
    """Return the repository's dedicated, non-source output directory."""

    return find_repository_root(repository_root) / MILESTONE_OUTPUT_RELATIVE


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _lexical_output_path(output: PathLike, repository: Path) -> Path:
    root = repository / MILESTONE_OUTPUT_RELATIVE
    requested = Path(output).expanduser()
    candidate = Path(
        os.path.normpath(str(requested if requested.is_absolute() else root / requested))
    )
    if candidate == root or not _is_within(candidate, root):
        raise UnsafePathError(
            f"output path must remain beneath {root}; refusing {requested}"
        )
    if candidate.name in {"", ".", ".."}:
        raise UnsafePathError(f"output path must name a file: {requested}")
    return candidate


def _open_directory_at(parent_fd: int, name: str, *, create: bool) -> int:
    if name in {"", ".", ".."} or os.sep in name:
        raise UnsafePathError(f"unsafe output directory component: {name!r}")
    if create:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
    try:
        child_fd = os.open(name, _DIRECTORY_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError as exc:
        raise UnsafePathError(f"output directory component does not exist: {name}") from exc
    except OSError as exc:
        raise UnsafePathError(
            f"output directory component is not a no-follow ordinary directory: {name}"
        ) from exc
    child_stat = os.fstat(child_fd)
    if not stat.S_ISDIR(child_stat.st_mode):
        os.close(child_fd)
        raise UnsafePathError(f"output path component is not a directory: {name}")
    return child_fd


def _open_output_parent(
    repository: Path,
    candidate: Path,
    *,
    create_parent: bool,
) -> int:
    """Open every output component with openat/O_NOFOLLOW and retain the parent."""

    required = MILESTONE_OUTPUT_RELATIVE.parts + candidate.relative_to(
        repository / MILESTONE_OUTPUT_RELATIVE
    ).parts[:-1]
    try:
        current_fd = os.open(repository, _DIRECTORY_FLAGS)
    except OSError as exc:
        raise UnsafePathError(
            f"repository root is not an ordinary no-follow directory: {repository}"
        ) from exc
    try:
        for component in required:
            next_fd = _open_directory_at(
                current_fd,
                component,
                create=create_parent,
            )
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _entry_stat(parent_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _identity(info: os.stat_result) -> tuple[int, int]:
    return int(info.st_dev), int(info.st_ino)


def _link_fd_no_replace(source_fd: int, destination_dir_fd: int, name: str) -> None:
    """Hard-link exactly ``source_fd`` using Linux linkat(AT_EMPTY_PATH)."""

    libc = ctypes.CDLL(None, use_errno=True)
    linkat = getattr(libc, "linkat", None)
    if linkat is None:
        raise UnsafePathError(
            "secure output publication requires Linux linkat(AT_EMPTY_PATH)"
        )
    linkat.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    linkat.restype = ctypes.c_int
    result = linkat(
        source_fd,
        ctypes.c_char_p(b""),
        destination_dir_fd,
        ctypes.c_char_p(os.fsencode(name)),
        _AT_EMPTY_PATH,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise OutputExistsError(f"refusing to overwrite existing output: {name}")
    raise UnsafePathError(
        "secure output publication with linkat(AT_EMPTY_PATH) failed: "
        f"{os.strerror(error_number)}"
    )


@dataclass
class OwnedOutputFile:
    """A unique partial whose descriptor permanently identifies its owned inode."""

    target: "SecureOutputTarget"
    name: str
    fd: int
    identity: tuple[int, int]
    closed: bool = False

    @property
    def path(self) -> Path:
        return self.target.path.with_name(self.name)

    def assert_same_inode(self) -> None:
        if self.closed or _identity(os.fstat(self.fd)) != self.identity:
            raise UnsafePathError("owned partial file descriptor changed identity")

    def close(self) -> None:
        if not self.closed:
            os.close(self.fd)
            self.closed = True


@dataclass
class SecureOutputTarget:
    """A final basename anchored to a retained, no-follow parent directory fd."""

    path: Path
    parent_fd: int
    parent_identity: tuple[int, int]
    closed: bool = False

    @property
    def name(self) -> str:
        return self.path.name

    def create_partial(self) -> OwnedOutputFile:
        if self.closed:
            raise RuntimeError("secure output target is closed")
        for _ in range(128):
            partial_name = f".{self.name}.partial-{secrets.token_hex(16)}"
            try:
                fd = os.open(partial_name, _PARTIAL_FLAGS, 0o600, dir_fd=self.parent_fd)
            except FileExistsError:
                continue
            os.fchmod(fd, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                os.close(fd)
                raise UnsafePathError("secure partial is not a regular file")
            return OwnedOutputFile(self, partial_name, fd, _identity(info))
        raise OutputExistsError("could not allocate a unique decoder partial output")

    def assert_path_binding(self) -> None:
        """Detect a renamed/replaced parent without using its path as authority."""

        try:
            current = os.stat(self.path.parent, follow_symlinks=False)
        except OSError as exc:
            raise UnsafePathError("output parent changed after it was securely opened") from exc
        if not stat.S_ISDIR(current.st_mode) or _identity(current) != self.parent_identity:
            raise UnsafePathError("output parent changed after it was securely opened")

    def publish(self, partial: OwnedOutputFile) -> None:
        if partial.target is not self:
            raise ValueError("partial belongs to a different secure output target")
        partial.assert_same_inode()
        self.assert_path_binding()
        _link_fd_no_replace(partial.fd, self.parent_fd, self.name)
        published = _entry_stat(self.parent_fd, self.name)
        if published is None or _identity(published) != partial.identity:
            raise UnsafePathError("published output does not match the validated partial inode")
        os.fsync(self.parent_fd)
        self.assert_path_binding()

    def unlink_owned(self, name: str, identity: tuple[int, int]) -> bool:
        """Unlink ``name`` only while it still refers to the inode we created."""

        current = _entry_stat(self.parent_fd, name)
        if current is None or _identity(current) != identity:
            return False
        os.unlink(name, dir_fd=self.parent_fd)
        return True

    def close(self) -> None:
        if not self.closed:
            os.close(self.parent_fd)
            self.closed = True

    def __enter__(self) -> "SecureOutputTarget":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def open_secure_output_target(
    output: PathLike,
    *,
    repository_root: PathLike | None = None,
    create_parent: bool = False,
    refuse_existing: bool = True,
) -> SecureOutputTarget:
    """Anchor a generated output to a no-follow directory descriptor."""

    repository = find_repository_root(repository_root)
    candidate = _lexical_output_path(output, repository)
    parent_fd = _open_output_parent(
        repository,
        candidate,
        create_parent=create_parent,
    )
    target = SecureOutputTarget(candidate, parent_fd, _identity(os.fstat(parent_fd)))
    try:
        existing = _entry_stat(parent_fd, target.name)
        if refuse_existing and existing is not None:
            raise OutputExistsError(f"refusing to overwrite existing output: {candidate}")
        if existing is not None and stat.S_ISDIR(existing.st_mode):
            raise UnsafePathError(f"output path is a directory: {candidate}")
        return target
    except BaseException:
        target.close()
        raise


def resolve_safe_output_path(
    output: PathLike,
    *,
    repository_root: PathLike | None = None,
    create_parent: bool = False,
    refuse_existing: bool = True,
) -> Path:
    """Validate a display path; writers must retain ``open_secure_output_target``."""

    with open_secure_output_target(
        output,
        repository_root=repository_root,
        create_parent=create_parent,
        refuse_existing=refuse_existing,
    ) as target:
        return target.path


safe_output_path = resolve_safe_output_path
