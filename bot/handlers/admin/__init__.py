from aiogram import Router

from .admin_panel import router as panel_router
from .admin_users import router as users_router
from .admin_promo import router as promo_router
from .admin_settings import router as settings_router
from .admin_broadcast import router as broadcast_router


def setup_admin_routers() -> Router:
    """Returns combined admin router."""
    admin_router = Router()
    admin_router.include_router(panel_router)
    admin_router.include_router(users_router)
    admin_router.include_router(promo_router)
    admin_router.include_router(settings_router)
    admin_router.include_router(broadcast_router)
    return admin_router
