"""Small, CPU-only runtime and peak-memory reporting helpers."""

from __future__ import annotations

from dataclasses import dataclass
import contextlib
import os
import resource
import sys
import threading
import time
from typing import Iterator


TARGET_MEMORY_BYTES = 512 * 1024 * 1024
HARD_MEMORY_LIMIT_BYTES = 1 * 1024 * 1024 * 1024


def current_peak_memory_bytes() -> int:
    """Return this process's peak resident set size in bytes.

    ``ru_maxrss`` is reported in KiB on Linux and bytes on macOS.  The
    project runs on Linux/Colab, but handling both units keeps the helper
    portable and avoids an optional monitoring dependency.
    """

    usage = resource.getrusage(resource.RUSAGE_SELF)
    if sys.platform == "darwin":
        return int(usage.ru_maxrss)
    return int(usage.ru_maxrss) * 1024


def current_child_peak_memory_bytes() -> int:
    """Return the waited-for child-process high-water RSS in bytes.

    This is deliberately separate from :func:`current_peak_memory_bytes`.
    ``RUSAGE_CHILDREN`` is the operating-system accounting scope used by the
    decoder wrapper after ``subprocess.run`` has reaped Node; callers must not
    label the self-RSS value as decoder memory.
    """

    usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    if sys.platform == "darwin":
        return int(usage.ru_maxrss)
    return int(usage.ru_maxrss) * 1024


def _proc_rss_bytes(pid: int) -> int | None:
    """Read one Linux process's current/high-water RSS from ``/proc``."""

    try:
        status = open(f"/proc/{pid}/status", "rt", encoding="ascii")
    except OSError:
        return None
    values: dict[str, int] = {}
    try:
        with status:
            for line in status:
                if line.startswith(("VmHWM:", "VmRSS:")):
                    fields = line.split()
                    if len(fields) >= 2 and fields[1].isdigit():
                        # Linux reports these status values in KiB.
                        values[fields[0][:-1]] = int(fields[1]) * 1024
    except OSError:
        return None
    if not values:
        return None
    return max(values.values())


class ChildRSSMonitor:
    """Sample one live child PID's RSS without attributing parent memory."""

    def __init__(self, pid: int, *, interval_seconds: float = 0.005) -> None:
        if pid <= 0:
            raise ValueError("pid must be positive")
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.pid = pid
        self.interval_seconds = interval_seconds
        self._peak_bytes: int | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def supported(self) -> bool:
        return sys.platform.startswith("linux") and os.path.isdir(f"/proc/{self.pid}")

    @property
    def peak_bytes(self) -> int | None:
        return self._peak_bytes

    def sample(self) -> int | None:
        value = _proc_rss_bytes(self.pid)
        if value is not None:
            self._peak_bytes = value if self._peak_bytes is None else max(self._peak_bytes, value)
        return value

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample()
            self._stop.wait(self.interval_seconds)

    def start(self) -> None:
        if not self.supported:
            raise RuntimeError("per-PID RSS monitoring requires Linux /proc")
        self.sample()
        self._thread = threading.Thread(
            target=self._run,
            name=f"scene-agent-rss-{self.pid}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> int | None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
        # The PID may have exited, so this is best-effort; all samples taken
        # while it was alive remain in _peak_bytes.
        self.sample()
        return self._peak_bytes


@dataclass(frozen=True)
class RuntimeMemoryReport:
    """Measured wall time and process peak memory for one operation."""

    elapsed_seconds: float
    peak_memory_bytes: int
    target_memory_bytes: int = TARGET_MEMORY_BYTES
    hard_memory_limit_bytes: int = HARD_MEMORY_LIMIT_BYTES
    memory_source: str = "process"

    @property
    def runtime_seconds(self) -> float:
        return self.elapsed_seconds

    @property
    def peak_memory_mib(self) -> float:
        return self.peak_memory_bytes / (1024 * 1024)

    @property
    def peak_rss_bytes(self) -> int:
        """Alias emphasizing that the value is a resident-set high-water mark."""

        return self.peak_memory_bytes

    @property
    def peak_memory_source(self) -> str:
        return self.memory_source

    @property
    def above_target(self) -> bool:
        return self.peak_memory_bytes > self.target_memory_bytes

    @property
    def above_hard_limit(self) -> bool:
        return self.peak_memory_bytes > self.hard_memory_limit_bytes

    def as_dict(self) -> dict[str, float | int | bool | str]:
        return {
            "elapsed_seconds": self.elapsed_seconds,
            "runtime_seconds": self.elapsed_seconds,
            "peak_memory_bytes": self.peak_memory_bytes,
            "peak_rss_bytes": self.peak_memory_bytes,
            "peak_memory_mib": self.peak_memory_mib,
            "memory_source": self.memory_source,
            "target_memory_bytes": self.target_memory_bytes,
            "hard_memory_limit_bytes": self.hard_memory_limit_bytes,
            "above_target": self.above_target,
            "above_hard_limit": self.above_hard_limit,
        }


@contextlib.contextmanager
def measure_runtime_memory() -> Iterator[dict[str, float | int]]:
    """Yield a mutable result dictionary populated when the block exits.

    This context manager is intentionally light-weight.  The operating system
    reports a process high-water mark; it does not pretend to measure a
    subprocess's private allocator state.
    """

    started = time.perf_counter()
    result: dict[str, float | int] = {}
    try:
        yield result
    finally:
        elapsed = time.perf_counter() - started
        peak = current_peak_memory_bytes()
        result.update(
            elapsed_seconds=elapsed,
            runtime_seconds=elapsed,
            peak_memory_bytes=peak,
            peak_memory_mib=peak / (1024 * 1024),
        )
