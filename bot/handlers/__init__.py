from aiogram import Router

from .user_start import router as start_router
from .campaigns import campaigns_router
from .accounts import router as accounts_router
from .database import router as database_router
from .compose import router as compose_router
from .delays import router as delays_router
from .mailing import router as mailing_router
from .stats import router as stats_router
from .logs import router as logs_router
from .profile import router as profile_router
from .subscription_handler import router as subscription_router
from .admin import setup_admin_routers
from .tools import router as tools_router
from .referral import router as referral_router


def setup_routers(admin_ids: list[int]) -> tuple[Router, Router]:
    """
    Returns (user_router, admin_router).
    Both should be registered separately so admin middleware only applies to admin_router.
    """
    user_router = Router()
    user_router.include_router(start_router)
    user_router.include_router(campaigns_router)
    user_router.include_router(profile_router)
    user_router.include_router(subscription_router)
    user_router.include_router(accounts_router)
    user_router.include_router(database_router)
    user_router.include_router(compose_router)
    user_router.include_router(delays_router)
    user_router.include_router(mailing_router)
    user_router.include_router(stats_router)
    user_router.include_router(logs_router)
    user_router.include_router(tools_router)
    user_router.include_router(referral_router)

    admin_router = setup_admin_routers()
    return user_router, admin_router
