"""Feature modules (PRD section 7, "Local server design").

Each feature exports a ``Feature`` (``features/base.py``) with a ``name``, an
``APIRouter`` and an optional ``on_startup`` hook; ``main.py`` mounts everything in
``FEATURES``. Features may use ``studio_server.core`` and ``features.base``, never each
other; shared logic moves into core.
"""

from studio_server.features import generations, settings, worker
from studio_server.features.base import Feature

FEATURES: list[Feature] = [settings.feature, worker.feature, generations.feature]

__all__ = ["FEATURES", "Feature"]
