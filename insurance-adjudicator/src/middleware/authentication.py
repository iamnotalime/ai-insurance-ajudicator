"""
JWT and API Key Authentication Middleware for Insurance Adjudication System
Provides dual authentication with RBAC role checking
"""

import logging
import hmac
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional, List

import jwt
from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from ..config.settings import settings


logger = logging.getLogger(__name__)

http_bearer = HTTPBearer(auto_error=False)


@dataclass
class AuthenticatedUser:
    """Represents an authenticated user with role information"""
    user_id: str
    roles: List[str] = field(default_factory=list)
    email: Optional[str] = None
    name: Optional[str] = None
    auth_method: str = "jwt"  # "jwt" or "api_key"
    scopes: List[str] = field(default_factory=list)

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_any_role(self, roles: List[str]) -> bool:
        return bool(set(self.roles) & set(roles))

    def is_admin(self) -> bool:
        return self.has_any_role(settings.security.admin_roles)

    def is_reviewer(self) -> bool:
        return self.has_any_role(settings.security.reviewer_roles)

    def is_viewer(self) -> bool:
        return self.has_any_role(settings.security.viewer_roles)


def create_jwt_token(
    user_id: str,
    roles: List[str],
    email: Optional[str] = None,
    name: Optional[str] = None,
    expiry_hours: Optional[int] = None,
) -> str:
    """Create a signed JWT token"""
    secret = settings.security.jwt_secret
    if not secret:
        raise ValueError("JWT_SECRET must be configured to create tokens")

    now = datetime.now(timezone.utc)
    expiry = now + timedelta(hours=expiry_hours or settings.security.jwt_expiry_hours)

    payload = {
        "sub": user_id,
        "roles": roles,
        "iat": now,
        "exp": expiry,
    }
    if email:
        payload["email"] = email
    if name:
        payload["name"] = name

    return jwt.encode(payload, secret, algorithm=settings.security.jwt_algorithm)


def _validate_jwt_token(token: str) -> AuthenticatedUser:
    """Validate a JWT token and return the authenticated user"""
    secret = settings.security.jwt_secret
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT authentication not configured",
        )

    try:
        payload = jwt.decode(
            token,
            secret,
            algorithms=[settings.security.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return AuthenticatedUser(
        user_id=payload.get("sub", ""),
        roles=payload.get("roles", []),
        email=payload.get("email"),
        name=payload.get("name"),
        auth_method="jwt",
    )


def _validate_api_key(api_key: str) -> AuthenticatedUser:
    """Validate an API key and return the authenticated user"""
    valid_keys = settings.api_keys
    if not valid_keys:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API key authentication not configured",
        )

    if not any(hmac.compare_digest(api_key, valid_key) for valid_key in valid_keys):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    # API key users get viewer-level access by default
    key_fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
    return AuthenticatedUser(
        user_id=f"api_key:{key_fingerprint}",
        roles=["viewer"],
        auth_method="api_key",
    )


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(http_bearer),
) -> AuthenticatedUser:
    """
    FastAPI dependency that extracts and validates authentication.
    Supports both JWT Bearer tokens and API keys.
    """
    # In development without auth config, allow anonymous access
    if settings.is_development and not settings.security.jwt_secret and not settings.api_keys:
        return AuthenticatedUser(
            user_id="dev-user",
            roles=["admin"],
            auth_method="dev",
        )

    # Try Bearer token (JWT)
    if credentials:
        return _validate_jwt_token(credentials.credentials)

    # Try API key header
    api_key = request.headers.get(settings.security.api_key_header)
    if api_key:
        return _validate_api_key(api_key)

    # Try Authorization header with API key prefix
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("ApiKey "):
        return _validate_api_key(auth_header[7:])

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required. Provide a Bearer token or API key.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_roles(*roles: str):
    """
    FastAPI dependency factory that requires the user to have specific roles.

    Usage:
        @router.post("/admin/action")
        async def admin_action(user: AuthenticatedUser = Depends(require_roles("admin"))):
            ...
    """
    async def _check_roles(
        user: AuthenticatedUser = Depends(get_current_user),
    ) -> AuthenticatedUser:
        if not user.has_any_role(list(roles)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required roles: {', '.join(roles)}",
            )
        return user

    return _check_roles
