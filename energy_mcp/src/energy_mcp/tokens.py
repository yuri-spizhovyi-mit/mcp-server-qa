"""Bearer-token verification (AUTH-D5): RS256 access-token JWTs (RFC 9068) on an injected clock.

`JwtVerifier` implements the SDK's `TokenVerifier` protocol. Every rejection is `None`,
never an exception, whatever the input. Time comes from the injected `clock` (epoch seconds),
never from PyJWT's own wall-clock checks, so tests can drive expiry (F-09).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from mcp.server.auth.provider import AccessToken

ALGORITHM = "RS256"
ACCESS_TOKEN_TYPES = frozenset({"at+jwt", "application/at+jwt"})  # RFC 9068 §4
REQUIRED_CLAIMS = ("iss", "aud", "exp", "iat", "sub", "client_id", "jti", "scope")  # RFC 9068 §2.2
# sub becomes acknowledged_by, which other callers' models read: plain ASCII only (fullmatch).
PRINCIPAL = re.compile(r"[A-Za-z0-9._-]{1,64}")


@dataclass(frozen=True)
class TokenSettings:
    issuer: str
    audience: str
    public_key_pem: bytes


class JwtVerifier:
    def __init__(self, settings: TokenSettings, clock: Callable[[], float]) -> None:
        self.settings = settings
        self.clock = clock
        self._key = load_pem_public_key(settings.public_key_pem)

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = jwt.decode(
                token, self._key, algorithms=[ALGORITHM], audience=self.settings.audience,
                issuer=self.settings.issuer,
                options={"require": list(REQUIRED_CLAIMS), "verify_exp": False, "verify_nbf": False,
                         "verify_iat": False},
            )
            header = jwt.get_unverified_header(token)  # covered by the signature checked above
        except (jwt.PyJWTError, ValueError, TypeError):
            return None
        if str(header.get("typ", "")).lower() not in ACCESS_TOKEN_TYPES or not self._in_time(claims):
            return None
        scope, sub, client_id = claims["scope"], claims["sub"], claims["client_id"]
        if not isinstance(scope, str) or not all(
                isinstance(v, str) and PRINCIPAL.fullmatch(v) for v in (sub, client_id)):
            return None
        return AccessToken(token=token, client_id=client_id, scopes=scope.split(), expires_at=int(claims["exp"]),
                           resource=self.settings.audience, subject=sub, claims={"iss": claims["iss"]})

    def _in_time(self, claims: dict[str, Any]) -> bool:
        """exp > now, iat <= now, nbf <= now when present; no leeway: the clock is ours."""
        now = self.clock()
        times = [claims[c] for c in ("exp", "iat", "nbf") if c in claims]
        if not all(isinstance(t, int | float) and not isinstance(t, bool) for t in times):
            return False
        return claims["exp"] > now and claims["iat"] <= now and claims.get("nbf", now) <= now
