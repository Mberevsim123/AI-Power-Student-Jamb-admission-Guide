"""
app/core/dependencies.py
Cross-cutting FastAPI dependency injectors.

Scope of this file:
  - Authentication / authorisation (get_current_user, get_admin_user)
  - Nothing else — service and repo factories live in their own route files
    or in app/core/factories.py if shared across multiple routers.
"""

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.user import User, UserRole
from app.repositories.repository import UserRepository
from app.utils.jwt import decode_access_token

_bearer = HTTPBearer()


# ---------------------------------------------------------------------------
# Shared repo factory — used here and re-exported for route files that need it
# ---------------------------------------------------------------------------


def get_user_repo(db: AsyncSession = Depends(get_db)) -> UserRepository:
    return UserRepository(db)


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


async def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    user_repo: UserRepository = Depends(get_user_repo),
) -> User:
    payload = decode_access_token(creds.credentials)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # FIX: validate sub explicitly — don't silently fall back to user_id=0
    user_id_str = payload.get("sub")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_id = int(user_id_str)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid token claims")
    user = await user_repo.get_by_id(user_id)

    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deactivated",
        )
    return user


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------


async def get_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role != UserRole.admin.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return current_user


async def get_consented_user(current_user: User = Depends(get_current_user)) -> User:
    if current_user.data_consent_at is None:
        raise HTTPException(
            403, "Accept data storage and AI processing via POST /api/v1/auth/consent first"
        )
    return current_user
