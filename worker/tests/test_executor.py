"""The executor thread, driven directly (no HTTP)."""

import itertools
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from studio_contracts import ErrorCode, Job, JobState, ModelState, SongParams
from studio_worker.engines.base import EngineOOM
from studio_worker.engines.fake import FakeEngine
from studio_worker.jobs.executor import JobExecutor, NotReadyError
from studio_worker.jobs.queue import QueueFullError
from studio_worker.jobs.store import JobStore
from studio_worker.operations.registry import get_operation
from studio_worker.operations.song_generate import SongGenerate

PARAMS = SongParams(style="s", lyrics="l")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def wait_until(predicate: Callable[[], bool], timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        time.sleep(0.005)


@pytest.fixture
def make_executor(tmp_path: Path) -> Iterator[Callable[..., JobExecutor]]:
    started: list[JobExecutor] = []

    def make(
        engine: FakeEngine | None = None, *, max_queue: int = 4, clock: Clock | None = None
    ) -> JobExecutor:
        store = JobStore(tmp_path / "jobs")
        store.load()
        executor = JobExecutor(
            store,
            engine or FakeEngine(),
            get_operation,
            max_queue=max_queue,
            **({"clock": clock} if clock else {}),
        )
        executor.start()
        started.append(executor)
        wait_until(lambda: executor.loader.state is not ModelState.loading)
        return executor

    yield make
    for executor in started:
        engine = executor.engine
        assert isinstance(engine, FakeEngine)
        if engine.stage_gate is not None:
            engine.stage_gate.set()
        executor.stop()


def finished(executor: JobExecutor, job_id: str) -> Job:
    wait_until(lambda: executor.store.get(job_id).state.is_terminal)
    return executor.store.get(job_id)


def submit(executor: JobExecutor, crid: str) -> Job:
    job, created = executor.submit(crid, SongGenerate(), PARAMS)
    assert created
    return job


def test_job_runs_through_every_stage(make_executor: Callable[..., JobExecutor]) -> None:
    executor = make_executor()
    job = finished(executor, submit(executor, "a").id)
    assert job.state is JobState.succeeded
    assert job.started_at is not None and job.finished_at is not None
    assert job.created_at <= job.started_at <= job.finished_at
    assert job.stage == "save" and job.progress is None
    expected = {"queue_s", "plan_s", "semantic_s", "synthesis_s", "decode_s", "save_s", "total_s"}
    assert set(job.timings) == expected
    assert isinstance(job.params_resolved["seed"], int)
    assert "seed" not in job.params  # params are what was sent; the seed is resolved
    assert {a.name for a in job.artifacts} >= {"audio.flac", "score.abc"}


def test_jobs_run_fifo_and_never_overlap(make_executor: Callable[..., JobExecutor]) -> None:
    engine = FakeEngine(stage_delay_s=0.002)
    executor = make_executor(engine)
    jobs = [submit(executor, f"c{i}") for i in range(4)]
    done = [finished(executor, j.id) for j in jobs]
    assert all(j.state is JobState.succeeded for j in done)
    starts = [j.started_at for j in done if j.started_at is not None]
    assert len(starts) == 4 and starts == sorted(starts)
    for earlier, later in itertools.pairwise(done):
        assert earlier.finished_at is not None and later.started_at is not None
        assert earlier.finished_at <= later.started_at
    assert engine.max_concurrent == 1
    assert engine.calls == ["plan", "semantic", "synthesis", "decode", "save"] * 4


def test_engine_failure_is_engine_error_and_the_next_job_runs(
    make_executor: Callable[..., JobExecutor],
) -> None:
    engine = FakeEngine(fail_at="synthesis", fail_with=ValueError("non-finite latents"))
    executor = make_executor(engine)
    job = finished(executor, submit(executor, "a").id)
    assert job.state is JobState.failed
    assert job.error is not None
    assert job.error.code is ErrorCode.engine_error and job.error.retryable is False
    assert job.error.details == {"type": "ValueError", "message": "non-finite latents"}
    assert job.artifacts == []
    assert list(executor.store.artifacts_dir(job.id).iterdir()) == []
    assert "synthesis_s" in job.timings

    engine.fail_at = None
    assert finished(executor, submit(executor, "b").id).state is JobState.succeeded


def test_oom_is_reported_as_oom(make_executor: Callable[..., JobExecutor]) -> None:
    engine = FakeEngine(fail_at="decode", fail_with=EngineOOM("CUDA out of memory"))
    executor = make_executor(engine)
    job = finished(executor, submit(executor, "a").id)
    assert job.error is not None and job.error.code is ErrorCode.oom
    assert job.error.retryable is True


def test_submit_is_refused_until_ready_and_after_a_load_failure(
    make_executor: Callable[..., JobExecutor], tmp_path: Path
) -> None:
    executor = make_executor(FakeEngine(load_error=RuntimeError("no BF16")))
    assert executor.loader.state is ModelState.error
    assert executor.loader.error == "RuntimeError: no BF16"
    with pytest.raises(NotReadyError):
        executor.submit("a", SongGenerate(), PARAMS)


def test_queue_cap_counts_waiting_jobs_only(make_executor: Callable[..., JobExecutor]) -> None:
    hold = threading.Event()
    executor = make_executor(FakeEngine(stage_gate=hold), max_queue=2)
    first = submit(executor, "run")
    wait_until(lambda: executor.running_job_id == first.id)
    submit(executor, "q1")
    submit(executor, "q2")
    with pytest.raises(QueueFullError):
        executor.submit("q3", SongGenerate(), PARAMS)
    # A repeat of a known request is not a new job, so the cap does not apply.
    _, created = executor.submit("q1", SongGenerate(), PARAMS)
    assert not created
    hold.set()


def test_idle_seconds_reset_when_a_job_starts(
    make_executor: Callable[..., JobExecutor],
) -> None:
    clock = Clock()
    hold = threading.Event()
    executor = make_executor(FakeEngine(stage_gate=hold), clock=clock)
    clock.now += 87
    assert executor.idle_seconds() == 87
    job = submit(executor, "a")
    wait_until(lambda: executor.running_job_id == job.id)
    assert executor.idle_seconds() == 0
    clock.now += 50
    assert executor.idle_seconds() == 0  # still running
    hold.set()
    finished(executor, job.id)
    wait_until(lambda: executor.running_job_id is None)
    assert executor.idle_seconds() == 0
    clock.now += 12
    assert executor.idle_seconds() == 12
