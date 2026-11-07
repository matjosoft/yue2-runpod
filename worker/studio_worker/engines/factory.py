"""Build the engine named by ``ENGINE``."""

from studio_worker.config import WorkerSettings
from studio_worker.engines.base import Engine
from studio_worker.engines.fake import FakeEngine
from studio_worker.engines.yue2 import Yue2Engine


def create_engine(settings: WorkerSettings) -> Engine:
    if settings.engine == "fake":
        return FakeEngine(
            load_delay_s=settings.fake_load_delay_s, stage_delay_s=settings.fake_stage_delay_s
        )
    # Constructing Yue2Engine imports nothing; the yue2 package loads in Engine.load().
    return Yue2Engine(
        model_repo=settings.yue2_model_repo,
        vae_repo=settings.yue2_vae_repo,
        model_revision=settings.yue2_model_revision,
        vae_revision=settings.yue2_vae_revision,
        hf_token=settings.hf_token,
        hf_home=settings.hf_home,
    )
