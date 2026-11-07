"""GPU summary for ``/v1/status``.

Read with ``nvidia-smi`` (mounted into containers by the NVIDIA runtime), so no ML
framework is imported here and the probe is engine-independent. Any failure gives
``None``: status must never fail because the GPU could not be described.
"""

import subprocess
import threading
import time
from collections.abc import Callable

from studio_contracts import GpuInfo

NVIDIA_SMI_QUERY = [
    "nvidia-smi",
    "--query-gpu=name,memory.total,memory.used",
    "--format=csv,noheader,nounits",
]
TIMEOUT_S = 2.0

Runner = Callable[[list[str]], str]


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S, check=True).stdout


def parse_nvidia_smi(output: str) -> GpuInfo | None:
    """First GPU from ``name, total MiB, used MiB`` lines; ``None`` if unparsable."""
    lines = output.strip().splitlines()
    if not lines:
        return None
    parts = [part.strip() for part in lines[0].rsplit(",", 2)]
    if len(parts) != 3 or not parts[0]:
        return None
    try:
        total, used = int(float(parts[1])), int(float(parts[2]))
    except ValueError:
        return None
    return GpuInfo(name=parts[0], vram_total_mb=total, vram_used_mb=used)


class GpuProbe:
    """Caches the reading for ``ttl_s`` so frequent status polls stay cheap."""

    def __init__(
        self,
        run: Runner = _run,
        *,
        ttl_s: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._run = run
        self._ttl_s = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._at: float | None = None
        self._value: GpuInfo | None = None

    def get(self) -> GpuInfo | None:
        with self._lock:
            now = self._clock()
            if self._at is not None and now - self._at < self._ttl_s:
                return self._value
            try:
                self._value = parse_nvidia_smi(self._run(NVIDIA_SMI_QUERY))
            except (OSError, subprocess.SubprocessError):
                self._value = None
            self._at = now
            return self._value
