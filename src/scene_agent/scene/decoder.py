"""Safe orchestration of the frozen Node compressed-PLY decoder contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Union

from .canonical import (
    CANONICAL_REQUIRED_RECORD_BYTES,
    validate_canonical_file_descriptor,
)
from .compressed import CompressedInspection, inspect_compressed_ply
from .errors import (
    DecoderInvocationError,
    DecoderUnavailableError,
    MemoryBudgetExceeded,
    OutputExistsError,
    SourceChangedError,
)
from .fingerprint import sha256_file
from .memory import (
    HARD_MEMORY_LIMIT_BYTES,
    RuntimeMemoryReport,
    ChildRSSMonitor,
    current_child_peak_memory_bytes,
)
from .paths import find_repository_root, open_secure_output_target


PathLike = Union[str, Path]
@dataclass(frozen=True)
class DecodeReport:
    """Evidence and resource report for one successful decoder invocation."""

    source_path: Path
    output_path: Path
    partial_output_path: Path
    source_sha256_before: str
    source_sha256_after: str
    source_gaussian_count: int
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    runtime: RuntimeMemoryReport

    @property
    def gaussian_count(self) -> int:
        return self.source_gaussian_count

    @property
    def elapsed_seconds(self) -> float:
        return self.runtime.elapsed_seconds

    @property
    def runtime_seconds(self) -> float:
        return self.runtime.elapsed_seconds

    @property
    def peak_memory_bytes(self) -> int:
        return self.runtime.peak_memory_bytes

    @property
    def peak_memory_mib(self) -> float:
        return self.runtime.peak_memory_mib

    @property
    def peak_rss_bytes(self) -> int:
        return self.runtime.peak_rss_bytes

    @property
    def memory_report(self) -> RuntimeMemoryReport:
        return self.runtime

    @property
    def peak_memory_source(self) -> str:
        return self.runtime.memory_source

    def as_dict(self) -> dict[str, object]:
        return {
            "source_path": str(self.source_path),
            "output_path": str(self.output_path),
            "partial_output_path": str(self.partial_output_path),
            "source_sha256_before": self.source_sha256_before,
            "source_sha256_after": self.source_sha256_after,
            "source_gaussian_count": self.source_gaussian_count,
            "command": self.command,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "runtime": self.runtime.as_dict(),
        }


def _default_output_name(source: Path) -> str:
    name = source.name
    suffix = ".compressed.ply"
    if name.endswith(suffix):
        return name[: -len(suffix)] + ".ply"
    if name.endswith(".ply"):
        return name[: -len(".ply")] + ".decoded.ply"
    return name + ".decoded.ply"


def _check_memory_estimate(inspection: CompressedInspection) -> None:
    # The decoder emits the required canonical float32 fields even though the source
    # is packed.  This lower bound catches obviously impossible jobs before
    # Node allocates its output.  It is deliberately conservative: actual
    # process high-water memory is still reported after invocation.
    estimated = inspection.vertex_count * CANONICAL_REQUIRED_RECORD_BYTES
    if estimated > HARD_MEMORY_LIMIT_BYTES:
        raise MemoryBudgetExceeded(
            f"decoded canonical payload would require at least {estimated} bytes, "
            "above the 1 GiB hard memory limit"
        )


def _run_node_process(
    command: tuple[str, ...],
    *,
    timeout_seconds: float | None,
    output_fd: int,
) -> tuple[int, str, str, RuntimeMemoryReport, bool]:
    """Run Node and measure this invocation's RSS, not parent/lifetime RSS."""

    started = time.perf_counter()
    if sys.platform.startswith("linux"):
        try:
            process = subprocess.Popen(
                list(command),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                pass_fds=(output_fd,),
            )
        except FileNotFoundError as exc:
            raise DecoderUnavailableError(
                f"could not start Node.js decoder using {command[0]!r}; "
                "install Node.js 20+ and ensure it is executable"
            ) from exc
        except OSError as exc:
            raise DecoderInvocationError(
                f"could not start frozen decoder command {list(command)!r}: {exc}"
            ) from exc

        monitor = ChildRSSMonitor(process.pid)
        try:
            try:
                monitor.start()
            except RuntimeError as exc:
                process.kill()
                process.communicate()
                raise DecoderInvocationError(
                    "per-PID Node RSS measurement is unavailable on this Linux host; "
                    "the decoder was not finalized"
                ) from exc
            timed_out = False
            try:
                stdout, stderr = process.communicate(timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                process.kill()
                stdout, stderr = process.communicate()
        finally:
            peak = monitor.stop()
        if peak is None:
            raise DecoderInvocationError(
                "per-PID Node RSS measurement produced no sample; decoder was not finalized"
            )
        runtime = RuntimeMemoryReport(
            elapsed_seconds=time.perf_counter() - started,
            peak_memory_bytes=peak,
            memory_source="node_pid_proc",
        )
        return int(process.returncode), stdout or "", stderr or "", runtime, timed_out

    # Linux provides the required per-PID path.  On another platform retain a
    # clearly labeled fallback rather than claiming RUSAGE_CHILDREN identifies
    # this one child when it is actually a lifetime high-water mark.
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            pass_fds=(output_fd,),
        )
    except FileNotFoundError as exc:
        raise DecoderUnavailableError(
            f"could not start Node.js decoder using {command[0]!r}; "
            "install Node.js 20+ and ensure it is executable"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        peak = current_child_peak_memory_bytes()
        runtime = RuntimeMemoryReport(
            elapsed_seconds=time.perf_counter() - started,
            peak_memory_bytes=peak,
            memory_source="children_lifetime_high_water",
        )
        raise DecoderInvocationError(
            f"compressed-PLY decoder timed out after {timeout_seconds} seconds"
        ) from exc
    except OSError as exc:
        raise DecoderInvocationError(
            f"could not start frozen decoder command {list(command)!r}: {exc}"
        ) from exc
    runtime = RuntimeMemoryReport(
        elapsed_seconds=time.perf_counter() - started,
        peak_memory_bytes=current_child_peak_memory_bytes(),
        memory_source="children_lifetime_high_water",
    )
    return int(completed.returncode), completed.stdout or "", completed.stderr or "", runtime, False


def decode_compressed_ply(
    source: PathLike,
    output: PathLike | None = None,
    *,
    repository_root: PathLike | None = None,
    node_executable: str = "node",
    decoder_script: PathLike | None = None,
    timeout_seconds: float | None = None,
) -> DecodeReport:
    """Decode a packed source through ``node scripts/decode_compressed_ply.mjs``.

    The source is read-only.  The Node process writes a private partial file
    beneath ``outputs/milestone1``; Python validates the canonical schema and
    source count, rechecks the source SHA-256, and atomically hard-links the
    partial into a new final path.  Existing final or partial paths are never
    overwritten.
    """

    if timeout_seconds is not None and timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive when supplied")
    source_path = Path(source).expanduser()
    if not source_path.is_file():
        raise FileNotFoundError(f"compressed source does not exist or is not a regular file: {source_path}")
    source_path = source_path.resolve()
    root = find_repository_root(repository_root)
    # Fingerprint before parsing and before invoking an external process.  A
    # later hash check catches a writer that modified the source concurrently.
    source_before = sha256_file(source_path)
    inspection = inspect_compressed_ply(source_path)
    _check_memory_estimate(inspection)

    node = shutil.which(node_executable)
    if node is None:
        raise DecoderUnavailableError(
            f"Node.js executable {node_executable!r} was not found. "
            "Install Node.js 20+ and ensure 'node' is on PATH."
        )
    script = (
        Path(decoder_script).expanduser().resolve()
        if decoder_script is not None
        else (root / "scripts" / "decode_compressed_ply.mjs").resolve()
    )
    if not script.is_file():
        raise DecoderUnavailableError(
            f"frozen compressed-PLY decoder script was not found at {script}. "
            "Provide scripts/decode_compressed_ply.mjs from the repository."
        )

    with open_secure_output_target(
        output if output is not None else _default_output_name(source_path),
        repository_root=root,
        create_parent=True,
        refuse_existing=True,
    ) as target:
        final = target.path
        if source_path == final:
            raise OutputExistsError("decoder output must not be the immutable source path")
        partial = target.create_partial()
        command = (node, str(script), str(source_path), str(partial.fd))
        completed = False
        try:
            returncode, stdout, stderr, runtime, timed_out = _run_node_process(
                command,
                timeout_seconds=timeout_seconds,
                output_fd=partial.fd,
            )
            if runtime.above_hard_limit:
                raise MemoryBudgetExceeded(
                    f"Node decoder child peak RSS was {runtime.peak_memory_bytes} bytes, "
                    "above the 1 GiB hard memory limit"
                )
            if timed_out:
                raise DecoderInvocationError(
                    f"compressed-PLY decoder timed out after {timeout_seconds} seconds"
                )
            if returncode != 0:
                detail = stderr.strip() or stdout.strip() or "no decoder diagnostics"
                raise DecoderInvocationError(
                    f"frozen compressed-PLY decoder exited with status {returncode}: {detail}"
                )
            partial.assert_same_inode()
            try:
                decoded = validate_canonical_file_descriptor(
                    partial.fd,
                    reject_nonfinite=True,
                )
            except Exception as exc:
                raise DecoderInvocationError(
                    f"decoder output failed canonical PLY validation: {exc}"
                ) from exc
            if decoded.vertex_count != inspection.vertex_count:
                raise DecoderInvocationError(
                    "decoder changed Gaussian count: "
                    f"source has {inspection.vertex_count}, output has {decoded.vertex_count}"
                )

            source_after = sha256_file(source_path)
            if source_after != source_before:
                raise SourceChangedError(
                    "compressed source SHA-256 changed during decoding; "
                    f"before={source_before}, after={source_after}"
                )
            target.publish(partial)

            # A final post-finalization check closes the race between the previous
            # hash and publication. Cleanup is inode-conditional in ``finally``.
            source_final = sha256_file(source_path)
            if source_final != source_before:
                raise SourceChangedError(
                    "compressed source SHA-256 changed before decoder finalization; "
                    f"before={source_before}, after={source_final}"
                )
            target.unlink_owned(partial.name, partial.identity)
            completed = True
            return DecodeReport(
                source_path=source_path,
                output_path=final,
                partial_output_path=partial.path,
                source_sha256_before=source_before,
                source_sha256_after=source_final,
                source_gaussian_count=inspection.vertex_count,
                command=command,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
                runtime=runtime,
            )
        finally:
            if not completed:
                target.unlink_owned(target.name, partial.identity)
            target.unlink_owned(partial.name, partial.identity)
            partial.close()


run_decoder = decode_compressed_ply
