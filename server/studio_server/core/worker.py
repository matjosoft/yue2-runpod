"""The link to the worker: effective settings, the backend, the check and the cached
capabilities (S-5, S-6). Shared by the poller and the ``settings``, ``worker`` and
``generations`` features, so it lives in core (docs/decisions.md).

Settings precedence for URL and token: saved in the database, then the environment
(``STUDIO_WORKER_URL``, ``STUDIO_WORKER_TOKEN``), then unset.
"""

import logging

import httpx

from studio_contracts import (
    Capabilities,
    ClientErrorCode,
    ErrorCode,
    ModelState,
    SettingsUpdate,
    SettingsView,
    WorkerCheck,
    WorkerCheckState,
    WorkerSummary,
)
from studio_server.config import ServerSettings
from studio_server.core.backend import (
    BackendConfig,
    BackendError,
    Closeable,
    MusicBackend,
    create_backend,
)
from studio_server.core.db import Database
from studio_server.core.errors import error_info

log = logging.getLogger("studio_server.worker")

SETTING_URL = "worker_url"
SETTING_TOKEN = "worker_token"

NOT_CONFIGURED = error_info(
    ClientErrorCode.not_configured, "Set the worker URL and token in Settings first"
)

_CHECK_STATE_FOR_CODE: dict[str, WorkerCheckState] = {
    ErrorCode.unauthorized.value: WorkerCheckState.unauthorized,
    ClientErrorCode.incompatible_worker.value: WorkerCheckState.incompatible,
}
_CHECK_STATE_FOR_MODEL: dict[ModelState, WorkerCheckState] = {
    ModelState.loading: WorkerCheckState.warming_up,
    ModelState.ready: WorkerCheckState.ready,
    ModelState.error: WorkerCheckState.error,
}


def not_configured_error() -> BackendError:
    return BackendError(NOT_CONFIGURED)


class WorkerLink:
    def __init__(
        self,
        db: Database,
        settings: ServerSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self._transport = transport
        self._backend: MusicBackend | None = None
        self._backend_key: tuple[str, str] | None = None
        self._retired: list[MusicBackend] = []
        self._capabilities: Capabilities | None = None
        self.last_check = WorkerCheck(
            state=WorkerCheckState.unreachable,
            error=error_info(
                ClientErrorCode.worker_unreachable, "The worker has not been checked yet"
            ),
            worker=None,
        )
        self.submitting: set[str] = set()
        """Generation ids whose submit is in flight in a request handler; the poller
        leaves them alone."""

    # --- settings ---------------------------------------------------------------------

    def effective(self) -> tuple[str | None, str | None]:
        url = self._db.get_setting(SETTING_URL) or self._settings.worker_url
        token = self._db.get_setting(SETTING_TOKEN) or self._settings.worker_token
        return url, token

    def is_configured(self) -> bool:
        url, token = self.effective()
        return url is not None and token is not None

    def settings_view(self) -> SettingsView:
        url, token = self.effective()
        return SettingsView(worker_url=url, token_set=token is not None)

    def update_settings(self, update: SettingsUpdate) -> SettingsView:
        """Omitted fields keep their saved value; ``null`` clears it."""
        if "worker_url" in update.model_fields_set:
            self._db.set_setting(SETTING_URL, update.worker_url)
        if "token" in update.model_fields_set:
            self._db.set_setting(SETTING_TOKEN, update.token)
        self._forget()
        log.info("settings saved", extra={"fields": sorted(update.model_fields_set)})
        return self.settings_view()

    # --- backend ----------------------------------------------------------------------

    def backend(self) -> MusicBackend:
        """The backend for the current settings. Raises ``BackendError``
        (``not_configured``) when the URL or token is missing."""
        url, token = self.effective()
        if url is None or token is None:
            raise not_configured_error()
        if self._backend is None or self._backend_key != (url, token):
            self._forget()
            self._backend = create_backend(
                BackendConfig(
                    kind=self._settings.backend_kind,
                    url=url,
                    token=token,
                    retry_base_s=self._settings.retry_base_s,
                    transport=self._transport,
                )
            )
            self._backend_key = (url, token)
        return self._backend

    def _forget(self) -> None:
        """Drop the backend and caches built from old settings. The old backend may be
        in use by the poller right now, so it is closed at shutdown, not here."""
        if self._backend is not None:
            self._retired.append(self._backend)
        self._backend = None
        self._backend_key = None
        self._capabilities = None

    async def aclose(self) -> None:
        for backend in [*self._retired, *([self._backend] if self._backend else [])]:
            if isinstance(backend, Closeable):
                await backend.aclose()
        self._retired.clear()
        self._backend = None

    # --- check and capabilities -------------------------------------------------------

    async def capabilities(self, *, refresh: bool = False) -> Capabilities:
        """Cached worker capabilities (S-6). Fetched when nothing is cached yet."""
        if self._capabilities is None or refresh:
            self._capabilities = await self.backend().capabilities()
        return self._capabilities

    async def check(self) -> WorkerCheck:
        """Test the saved settings (S-5) and cache the result for ``/api/health``."""
        self.last_check = await self._check()
        return self.last_check

    async def _check(self) -> WorkerCheck:
        try:
            backend = self.backend()
            caps = await backend.capabilities()
            status = await backend.status()
        except BackendError as exc:
            if exc.code == ClientErrorCode.not_configured.value:
                state = WorkerCheckState.not_configured
            else:
                state = _CHECK_STATE_FOR_CODE.get(exc.code, WorkerCheckState.unreachable)
            return WorkerCheck(state=state, error=exc.info, worker=None)
        self._capabilities = caps
        state = _CHECK_STATE_FOR_MODEL[status.model.state]
        error = None
        if state is WorkerCheckState.error:
            error = error_info(
                ErrorCode.not_ready,
                "The model failed to load on the worker",
                details={"model_error": status.model.error},
                retryable=False,
            )
        summary = WorkerSummary(
            api_version=caps.api_version,
            worker_version=caps.worker_version,
            model_state=status.model.state,
        )
        return WorkerCheck(state=state, error=error, worker=summary)
