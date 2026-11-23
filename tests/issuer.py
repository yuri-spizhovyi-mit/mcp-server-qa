"""A test-only token issuer: mints access-token JWTs with a key made at test time.

Not a test module (rtm and pytest skip it). It can mint exactly the broken tokens the tests
need: any claim overridden or dropped, another key, another algorithm, another `typ`.
The key never leaves the test process; nothing here is a secret worth keeping.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import itertools
import json
from typing import Any

import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from energy_mcp.tokens import TokenSettings

ISSUER = "https://issuer.test"
AUDIENCE = "http://127.0.0.1:8000/mcp"
JANUARY = 1768208400.0  # 2026-01-12T09:00:00Z, the tests' clock start


def new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _segment(obj: dict[str, Any]) -> str:
    return _b64(json.dumps(obj, separators=(",", ":")).encode("utf-8"))


class Issuer:
    def __init__(self, issuer: str = ISSUER, audience: str = AUDIENCE) -> None:
        self.issuer, self.audience = issuer, audience
        self.key = new_key()
        self.public_pem = self.key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.settings = TokenSettings(issuer=issuer, audience=audience, public_key_pem=self.public_pem)
        self._jti = itertools.count(1)

    def claims(self, *, scope: Any = "meters:read", sub: Any = "op-1", client_id: Any = "agent-1",
               now: float = JANUARY, ttl: int = 3600, drop: tuple[str, ...] = (), **extra: Any) -> dict[str, Any]:
        claims = {"iss": self.issuer, "aud": self.audience, "sub": sub, "client_id": client_id, "scope": scope,
                  "iat": int(now), "exp": int(now) + ttl, "jti": f"jti-{next(self._jti)}"}
        claims.update(extra)
        for name in drop:
            claims.pop(name, None)
        return claims

    def mint(self, *, key: rsa.RSAPrivateKey | None = None, alg: str = "RS256", typ: str | None = "at+jwt",
             **claim_args: Any) -> str:
        claims = self.claims(**claim_args)
        headers = {} if typ is None else {"typ": typ}
        if alg == "RS256":
            token = jwt.encode(claims, key or self.key, algorithm="RS256", headers=headers)
            if typ is None:  # PyJWT adds typ "JWT" by default; rebuild the header without it
                _, payload, _ = token.split(".")
                return self._signed({"alg": "RS256"}, payload, key or self.key)
            return token
        if alg == "none":
            return f"{_segment({'alg': 'none', **headers})}.{_segment(claims)}."
        if alg == "HS256":  # an HMAC token whose secret is our public key PEM: the classic confusion attack
            signing_input = f"{_segment({'alg': 'HS256', **headers})}.{_segment(claims)}"
            mac = hmac.new(self.public_pem, signing_input.encode("ascii"), hashlib.sha256).digest()
            return f"{signing_input}.{_b64(mac)}"
        raise ValueError(alg)

    def _signed(self, header: dict[str, Any], payload: str, key: rsa.RSAPrivateKey) -> str:
        signing_input = f"{_segment(header)}.{payload}"
        signature = key.sign(signing_input.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        return f"{signing_input}.{_b64(signature)}"

    def tampered(self, **claim_args: Any) -> str:
        """A real token whose payload was swapped for one granting more, signature kept."""
        header, _, signature = self.mint().split(".")
        return f"{header}.{_segment(self.claims(**claim_args))}.{signature}"
