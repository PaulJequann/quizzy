from typing import Annotated

from fastapi import Depends
from fastapi_users import BaseUserManager, FastAPIUsers, IntegerIDMixin
from fastapi_users.authentication import AuthenticationBackend, CookieTransport
from fastapi_users.authentication.strategy.db import DatabaseStrategy
from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase
from fastapi_users_db_sqlalchemy.access_token import SQLAlchemyAccessTokenDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.database import get_auth_session
from app.auth.models import AccessToken, User
from app.config import get_settings


async def get_user_database(session: Annotated[AsyncSession, Depends(get_auth_session)]):
    yield SQLAlchemyUserDatabase(session, User)


class UserManager(IntegerIDMixin, BaseUserManager[User, int]):
    pass


async def get_user_manager(database: Annotated[SQLAlchemyUserDatabase, Depends(get_user_database)]):
    yield UserManager(database)


async def get_database_strategy(session: Annotated[AsyncSession, Depends(get_auth_session)]):
    return DatabaseStrategy(
        SQLAlchemyAccessTokenDatabase(session, AccessToken), lifetime_seconds=43200
    )


def get_cookie_transport():
    return CookieTransport(
        cookie_name="quizzy_session",
        cookie_max_age=43200,
        cookie_secure=get_settings().app_base_url.startswith("https://"),
    )


auth_backend = AuthenticationBackend(
    name="cookie",
    transport=get_cookie_transport(),
    get_strategy=get_database_strategy,
)
fastapi_users = FastAPIUsers[User, int](get_user_manager, [auth_backend])
current_user = fastapi_users.current_user(active=True)
current_admin = fastapi_users.current_user(active=True, superuser=True)
