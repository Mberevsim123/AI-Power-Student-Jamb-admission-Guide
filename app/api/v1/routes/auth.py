"""Account authentication, consent and deletion."""

from typing import Literal

from fastapi import APIRouter, Depends, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_current_user
from app.core.rate_limit import auth_limiter
from app.db.session import get_db
from app.models.user import User
from app.schemas.schema import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])
_bearer = HTTPBearer()


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new student account",
)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(auth_limiter),
):
    svc = AuthService(db)
    return await svc.register(body)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Login and receive JWT access + refresh tokens",
)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
    _rate: None = Depends(auth_limiter),
):
    svc = AuthService(db)
    return await svc.login(body)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Rotate refresh token and get a new access token",
)
async def refresh(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    svc = AuthService(db)
    return await svc.refresh(body.refresh_token)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the current session",
)
async def logout(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
):
    svc = AuthService(db)
    await svc.logout(creds.credentials)


class ConsentRequest(BaseModel):
    data_consent: Literal[True]


@router.post("/consent")
async def consent(
    body: ConsentRequest, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Consent to storing account/profile/chat data and sending context to the AI provider."""
    from datetime import datetime, timezone

    user.data_consent_at = datetime.now(timezone.utc)
    await db.commit()
    return {"data_consent_at": user.data_consent_at}


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return user


@router.delete("/me", status_code=204)
async def delete_account(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """Delete the account and its stored profile, messages, feedback, and tokens."""
    await db.delete(user)
    await db.commit()
