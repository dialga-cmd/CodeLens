"""Who is calling, and whether anyone has to be.

The demo exists so that a judge can open a URL and use it, which means sign-in
cannot be a wall. Firebase is therefore optional: configure it and every request
must carry a valid ID token, leave it out and the API runs in guest mode and says
so in ``/health``.

Either way the identity is the same shape, so nothing downstream has to care which
mode it is running in. A guest is a real subject as far as the rest of the
application is concerned - it just is not a verified one, and its uid is derived
from the client address so rate limits still work.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any

import firebase_admin
from fastapi import HTTPException, Query, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from firebase_admin import auth, credentials

security = HTTPBearer(auto_error=False)

# Set by initialize_firebase(). False means guest mode.
_auth_enabled = False
_warnings: list[str] = []


def auth_enabled() -> bool:
    return _auth_enabled


def auth_warnings() -> list[str]:
    """What was misconfigured, if anything. Safe to expose: no secrets."""
    return list(_warnings)


def initialize_firebase() -> None:
    """Configure Firebase if credentials are present; otherwise run as a guest API.

    Credentials are only taken from somewhere this service was pointed at: the
    service account JSON in an environment variable, a path to it in an
    environment variable, or - only when CODELENS_FIREBASE_APPLICATION_DEFAULT is
    set - the application default credentials of the machine. Nothing is inferred,
    so "does this deployment require sign-in?" is answered by its configuration
    rather than discovered by a judge through a 401.
    """
    global _auth_enabled

    if _env_flag("CODELENS_REQUIRE_AUTH") and not _configure_firebase():
        _auth_enabled = False
        _warnings.append(
            "CODELENS_REQUIRE_AUTH is set but no Firebase credentials were found, "
            "so the API is running in guest mode."
        )
        return

    _auth_enabled = _configure_firebase()
    if not _auth_enabled:
        print("Firebase is not configured - CodeLens is running in guest mode.", flush=True)


def _configure_firebase() -> bool:
    if firebase_admin._apps:
        return True

    service_account_json = os.getenv("FIREBASE_SERVICE_ACCOUNT", "").strip()
    if service_account_json:
        try:
            firebase_admin.initialize_app(credentials.Certificate(json.loads(service_account_json)))
            return True
        except (ValueError, TypeError) as error:
            _warnings.append(f"FIREBASE_SERVICE_ACCOUNT is not valid JSON: {error}")

    service_account_path = os.getenv("FIREBASE_SERVICE_ACCOUNT_PATH", "").strip()
    if service_account_path:
        if os.path.isfile(service_account_path):
            try:
                firebase_admin.initialize_app(credentials.Certificate(service_account_path))
                return True
            except Exception as error:  # noqa: BLE001 - reported, not raised
                _warnings.append(f"FIREBASE_SERVICE_ACCOUNT_PATH could not be used: {error}")
        else:
            _warnings.append(f"FIREBASE_SERVICE_ACCOUNT_PATH does not exist: {service_account_path}")

    if _env_flag("CODELENS_FIREBASE_APPLICATION_DEFAULT"):
        # Opt-in only. Ambient application default credentials can appear on a
        # machine that was never meant to run this service, and silently
        # requiring sign-in because of them would be worse than not having the
        # feature: a judge would meet a 401 instead of a demo.
        try:
            firebase_admin.initialize_app()
            return True
        except Exception as error:  # noqa: BLE001 - reported, not raised
            _warnings.append(f"Application default credentials could not be used: {error}")

    return False


async def verify_token(
    request: Request,
    res: HTTPAuthorizationCredentials = Security(security),
    token: str | None = Query(default=None),
) -> dict[str, Any]:
    """The caller's identity, verified when sign-in is required.

    The token is read from the Authorization header, or from ``?token=`` because
    ``EventSource`` cannot set headers on a browser's EventSource request.
    """
    id_token = (res.credentials if res and res.credentials else None) or token

    if not _auth_enabled:
        return _guest(request)

    if not id_token:
        raise HTTPException(status_code=401, detail="Authentication token missing")

    try:
        return auth.verify_id_token(id_token)
    except Exception as error:  # noqa: BLE001 - firebase raises several types
        raise HTTPException(status_code=401, detail=f"Invalid authentication credentials: {error}") from error


def _guest(request: Request) -> dict[str, Any]:
    """A stable, non-identifying subject for an unauthenticated caller.

    The uid is derived from the client address, so two guests behind the same
    address share a rate-limit bucket and nobody is able to claim to be someone
    else. It is not a credential and is not stored.
    """
    client = getattr(request, "client", None)
    host = getattr(client, "host", "") or "unknown"
    digest = hashlib.sha256(f"guest:{host}".encode("utf-8")).hexdigest()[:16]
    return {
        "uid": f"guest-{digest}",
        "email": None,
        "name": None,
        "auth": "guest",
    }


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").lower() in {"1", "true", "yes", "on"}
