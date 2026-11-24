"""
app/services/auth_service.py
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.user import RefreshToken, User, UserProfile
from app.repositories.repository import UserRepository
from app.schemas.schema import LoginRequest, RegisterRequest, TokenResponse
from app.utils.hashing import hash_password, verify_password
from app.utils.jwt import create_access_token, decode_access_token


class AuthService:
    def __init__(self, db: AsyncSession):
        self.repo = UserRepository(db)
        self.db = db

    async def register(self, body: RegisterRequest) -> User:
        if await self.repo.get_by_email(body.email):
            raise HTTPException(status_code=400, detail="Email already registered")
        if await self.repo.get_by_username(body.username):
            raise HTTPException(status_code=400, detail="Username taken")

        user = User(
            email=body.email,
            username=body.username,
            hashed_password=hash_password(body.password),
            data_consent_at=datetime.now(timezone.utc),
        )
        user = await self.repo.create(user)

        profile = UserProfile(user_id=user.id)
        await self.repo.upsert_profile(profile)
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def login(self, body: LoginRequest) -> TokenResponse:
        user = await self.repo.get_by_email(body.email)
        if not user or not verify_password(body.password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
            )
        if not user.is_active:
            raise HTTPException(status_code=403, detail="Account deactivated")

        access_token = create_access_token({"sub": str(user.id)})
        refresh_token = await self._issue_refresh_token(user.id)
        await self.repo.update_last_login(user.id)
        await self.db.commit()

        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
        )

    async def refresh(self, raw_token: str) -> TokenResponse:
        token_hash = _sha256(raw_token)
        record = await self.repo.get_refresh_token(token_hash)

        if not record or record.revoked:
            raise HTTPException(status_code=401, detail="Invalid refresh token")
        if record.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
            raise HTTPException(status_code=401, detail="Refresh token expired")

        user = await self.repo.get_by_id(record.user_id)
        if not user or not user.is_active:
            raise HTTPException(status_code=401, detail="Account inactive")

        # Rotate: revoke old, issue new
        await self.repo.revoke_refresh_token(token_hash)
        new_access = create_access_token({"sub": str(record.user_id)})
        new_refresh = await self._issue_refresh_token(record.user_id, family_id=record.family_id)
        await self.db.commit()

        return TokenResponse(access_token=new_access, refresh_token=new_refresh)

    async def logout(self, access_token: str) -> None:
        from sqlalchemy import update

        payload = decode_access_token(access_token)
        try:
            user_id = int(payload["sub"])
        except (TypeError, KeyError, ValueError):
            raise HTTPException(status_code=401, detail="Invalid access token")
        await self.db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id)
            .values(revoked=True, revoked_at=datetime.now(timezone.utc))
        )
        await self.db.commit()

    async def _issue_refresh_token(self, user_id: int, family_id: str | None = None) -> str:
        raw = secrets.token_urlsafe(48)
        token_hash = _sha256(raw)
        if not family_id:
            family_id = secrets.token_hex(16)

        expires = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
        record = RefreshToken(
            user_id=user_id,
            token_hash=token_hash,
            family_id=family_id,
            expires_at=expires,
        )
        await self.repo.save_refresh_token(record)
        return raw


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
