from __future__ import annotations

import asyncio
import os
import logging
from typing import Annotated

import jwt
from fastapi import Header, HTTPException
from jwt import PyJWKClient

_jwk_client: PyJWKClient | None = None
logger = logging.getLogger(__name__)


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
        # PyJWKClient may fetch/refresh Clerk's JWKS over HTTP. Keep that
        # blocking network call off FastAPI's event loop.
        signing_key = (await asyncio.to_thread(_jwk_client.get_signing_key_from_jwt, token)).key
        # Clerk and the API host can differ slightly in clock time; accept a
        # small skew while still rejecting materially premature/expired tokens.
        claims = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256"],
            issuer=issuer,
            leeway=60,
            options={"require": ["exp", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        logger.info("Clerk session token rejected: expired")
        raise HTTPException(status_code=401, detail="Your sign-in session expired. Sign in again.") from exc
    except jwt.ImmatureSignatureError as exc:
        logger.warning("Clerk session token rejected: not yet valid (API clock may be behind Clerk)")
        raise HTTPException(status_code=401, detail="The API server clock is behind the sign-in token. Synchronize the API host clock and retry.") from exc
    except jwt.InvalidIssuerError as exc:
        logger.warning("Clerk session token rejected: issuer mismatch")
        raise HTTPException(status_code=401, detail="The sign-in token is from a different Clerk instance. Check CLERK_ISSUER.") from exc
    except jwt.InvalidSignatureError as exc:
        logger.warning("Clerk session token rejected: signature mismatch")
        raise HTTPException(status_code=401, detail="The sign-in token signature does not match the configured Clerk JWKS.") from exc
    except jwt.PyJWKClientError as exc:
        logger.error("Clerk JWKS verification failed (%s)", type(exc).__name__)
        raise HTTPException(status_code=503, detail="The API could not verify the session using the configured Clerk JWKS.") from exc
    except (jwt.PyJWTError, OSError) as exc:
        logger.warning("Clerk session token rejected (%s)", type(exc).__name__)
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
