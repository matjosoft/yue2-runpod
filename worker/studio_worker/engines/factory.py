"""Build the engine named by ``ENGINE``."""

from studio_worker.config import WorkerSettings
from studio_worker.engines.base import Engine
from studio_worker.engines.fake import FakeEngine


def create_engine(settings: WorkerSettings) -> Engine:
    if settings.engine == "fake":
        return FakeEngine(
            load_delay_s=settings.fake_load_delay_s, stage_delay_s=settings.fake_stage_delay_s
        )
    # ENGINE=yue2: Yue2Engine (engines/yue2.py) arrives in milestone M4.
    raise NotImplementedError("ENGINE=yue2 is not implemented yet (milestone M4); use ENGINE=fake")
