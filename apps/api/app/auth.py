from __future__ import annotations

import os
from typing import Annotated

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

_jwk_client: PyJWKClient | None = None


def _auth_required() -> bool:
    configured = os.environ.get("AUTH_REQUIRED")
    if configured is not None:
        return configured.strip().casefold() in {"1", "true", "yes", "on"}
    return os.environ.get("ENVIRONMENT", "development").casefold() in {"production", "prod"}


async def get_current_user_id(authorization: Annotated[str | None, Header()] = None) -> str | None:
    if not authorization:
        if _auth_required():
            raise HTTPException(status_code=401, detail="Sign in is required to use this API.")
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.casefold() != "bearer" or not token.strip():
        raise HTTPException(status_code=401, detail="Use a valid Bearer session token.")

    issuer = os.environ.get("CLERK_ISSUER", "").strip().rstrip("/")
    jwks_url = os.environ.get("CLERK_JWKS_URL", "").strip() or (f"{issuer}/.well-known/jwks.json" if issuer else "")
    if not issuer or not jwks_url:
        raise HTTPException(status_code=503, detail="Clerk verification is not configured on the API.")
    global _jwk_client
    if _jwk_client is None:
        _jwk_client = PyJWKClient(jwks_url, cache_keys=True)
    try:
        signing_key = _jwk_client.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, signing_key, algorithms=["RS256"], issuer=issuer, options={"require": ["exp", "iss", "sub"]})
    except (jwt.PyJWTError, jwt.PyJWKClientError, OSError) as exc:
        raise HTTPException(status_code=401, detail="The session token is invalid or expired.") from exc

    authorized_party = claims.get("azp")
    web_origin = os.environ.get("WEB_ORIGIN", "http://localhost:3000").strip().rstrip("/")
    configured_origins = os.environ.get("AUTHORIZED_PARTIES", "").strip() or web_origin
    allowed_origins = {origin.strip().rstrip("/") for origin in configured_origins.split(",") if origin.strip()}
    if allowed_origins and (
        not isinstance(authorized_party, str)
        or authorized_party.rstrip("/") not in allowed_origins
    ):
        raise HTTPException(status_code=401, detail="The session token was issued to an untrusted application origin.")
    return str(claims["sub"])
