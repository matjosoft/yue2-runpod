"""GPU summary for ``/v1/status``: parsing ``nvidia-smi`` output, failure cases and caching."""

import subprocess

import pytest

from studio_contracts import GpuInfo
from studio_worker.gpu import NVIDIA_SMI_QUERY, GpuProbe, parse_nvidia_smi


def test_parse_first_gpu() -> None:
    out = "NVIDIA GeForce RTX 4090, 24564, 11500\nNVIDIA GeForce RTX 4090, 24564, 3\n"
    assert parse_nvidia_smi(out) == GpuInfo(
        name="NVIDIA GeForce RTX 4090", vram_total_mb=24564, vram_used_mb=11500
    )


@pytest.mark.parametrize("out", ["", "\n", "garbage", "A, B, C", "X, 1"])
def test_parse_garbage_is_none(out: str) -> None:
    assert parse_nvidia_smi(out) is None


def test_probe_passes_the_query_and_caches() -> None:
    calls: list[list[str]] = []
    now = [100.0]

    def run(cmd: list[str]) -> str:
        calls.append(cmd)
        return "GPU, 10, 5\n"

    probe = GpuProbe(run=run, ttl_s=5.0, clock=lambda: now[0])
    assert probe.get() == GpuInfo(name="GPU", vram_total_mb=10, vram_used_mb=5)
    now[0] += 4.0
    probe.get()
    assert calls == [NVIDIA_SMI_QUERY]
    now[0] += 2.0
    probe.get()
    assert len(calls) == 2


@pytest.mark.parametrize(
    "exc",
    [
        FileNotFoundError("nvidia-smi"),
        subprocess.TimeoutExpired("nvidia-smi", 2.0),
        subprocess.CalledProcessError(9, "nvidia-smi"),
        OSError("boom"),
    ],
)
def test_probe_failure_is_none(exc: Exception) -> None:
    def run(cmd: list[str]) -> str:
        raise exc

    assert GpuProbe(run=run).get() is None
