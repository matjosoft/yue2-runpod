"""``settings`` feature: worker URL and write-only token (S-5)."""

from fastapi import APIRouter

from studio_contracts import SettingsUpdate, SettingsView
from studio_server.core.errors import ERROR_RESPONSES
from studio_server.core.runtime import Runtime
from studio_server.features.base import Feature

router = APIRouter(prefix="/api/settings", tags=["settings"], responses=ERROR_RESPONSES)


@router.get("", response_model=SettingsView)
async def get_settings(rt: Runtime) -> SettingsView:
    """The effective worker URL, and whether a token is set. Never the token."""
    return rt.link.settings_view()


@router.put("", response_model=SettingsView)
async def put_settings(body: SettingsUpdate, rt: Runtime) -> SettingsView:
    """Omitted fields keep their saved value; ``null`` clears it (the environment
    default then applies); a value replaces it."""
    return rt.link.update_settings(body)


feature = Feature(name="settings", router=router)
