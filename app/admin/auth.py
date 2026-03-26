"""Admin authentication and authorization."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from app.config import settings


class AdminUser(BaseModel):
    """Represents an authenticated admin user."""

    username: str
    authenticated_at: datetime


class AdminSession(BaseModel):
    """Admin session with token."""

    token: str
    user: AdminUser
    expires_at: datetime


# In-memory session store (for production, use Redis or database)
_admin_sessions: dict[str, AdminSession] = {}

# Session duration
SESSION_DURATION_HOURS = 24


def _hash_token(token: str) -> str:
    """Hash a token for secure storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def verify_credentials(username: str, password: str) -> bool:
    """Verify admin credentials."""
    # Use secrets.compare_digest to prevent timing attacks
    username_match = secrets.compare_digest(
        username.encode(), settings.admin_username.encode()
    )
    password_match = secrets.compare_digest(
        password.encode(), settings.admin_password.encode()
    )
    return username_match and password_match


def create_session(username: str) -> AdminSession:
    """Create a new admin session."""
    token = secrets.token_urlsafe(32)
    token_hash = _hash_token(token)

    session = AdminSession(
        token=token,
        user=AdminUser(
            username=username,
            authenticated_at=datetime.now(timezone.utc),
        ),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=SESSION_DURATION_HOURS),
    )

    _admin_sessions[token_hash] = session
    return session


def get_session(token: str) -> AdminSession | None:
    """Get a session by token."""
    token_hash = _hash_token(token)
    session = _admin_sessions.get(token_hash)

    if session is None:
        return None

    # Check expiration
    if session.expires_at < datetime.now(timezone.utc):
        # Clean up expired session
        del _admin_sessions[token_hash]
        return None

    return session


def invalidate_session(token: str) -> bool:
    """Invalidate a session."""
    token_hash = _hash_token(token)
    if token_hash in _admin_sessions:
        del _admin_sessions[token_hash]
        return True
    return False


def cleanup_expired_sessions() -> int:
    """Remove all expired sessions. Returns count of removed sessions."""
    now = datetime.now(timezone.utc)
    expired_keys = [
        key for key, session in _admin_sessions.items() if session.expires_at < now
    ]
    for key in expired_keys:
        del _admin_sessions[key]
    return len(expired_keys)


async def get_current_admin(
    admin_token: Annotated[str | None, Cookie()] = None,
) -> AdminUser:
    """Dependency to get the current authenticated admin user."""
    if not admin_token:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Cookie"},
        )

    session = get_session(admin_token)
    if not session:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired session",
            headers={"WWW-Authenticate": "Cookie"},
        )

    return session.user


async def get_optional_admin(
    admin_token: Annotated[str | None, Cookie()] = None,
) -> AdminUser | None:
    """Dependency to get the current admin user if authenticated, or None."""
    if not admin_token:
        return None

    session = get_session(admin_token)
    return session.user if session else None


# Type alias for dependency injection
CurrentAdmin = Annotated[AdminUser, Depends(get_current_admin)]
OptionalAdmin = Annotated[AdminUser | None, Depends(get_optional_admin)]
