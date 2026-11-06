"""Signing in with any OpenID Connect issuer: the authorization code flow with PKCE.

Only the issuer's URL is configured; everything else comes from its discovery document. The
browser carries an :class:`Attempt` between the redirect out and the callback (sealed, see
:mod:`evalution.api.auth`), and the id token that comes back is checked here in full: signature
against the issuer's published keys, issuer, audience, expiry and the attempt's nonce. A token
signed with a shared secret (``HS*``) or not at all (``none``) is refused.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
import jwt

from evalution.accounts.settings import AuthSettings

SCOPES = "openid email profile"
CALLBACK = "/auth/callback"
#: Public-key signatures only.
ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}
)
#: Clock difference tolerated between here and the issuer, in seconds.
LEEWAY = 60
TIMEOUT = 10.0


class SignInError(RuntimeError):
    """Signing in failed. The message never holds a code, a token or the issuer's reply."""


@dataclass(frozen=True)
class Attempt:
    """One sign-in in progress: what the callback must see again."""

    state: str
    nonce: str
    verifier: str
    next: str


@dataclass(frozen=True)
class Identity:
    """Who the issuer says signed in."""

    issuer: str
    subject: str
    email: str
    name: str


def _challenge(verifier: str) -> str:
    """PKCE ``S256``."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class Issuer:
    """The configured issuer. Its discovery document and keys are fetched once and kept; the keys
    are fetched again when a token names one not seen yet (the issuer rotated them)."""

    def __init__(
        self, settings: AuthSettings, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._discovery: dict[str, Any] | None = None
        self._keys: jwt.PyJWKSet | None = None

    @property
    def redirect_uri(self) -> str:
        return self.settings.public_url + CALLBACK

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self._transport, timeout=TIMEOUT)

    async def _get(self, url: str) -> Any:
        try:
            async with self._client() as http:
                response = await http.get(url)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SignInError(f"the issuer can't be reached ({type(exc).__name__})") from None

    async def discovery(self) -> dict[str, Any]:
        """The issuer's discovery document, its ``issuer`` checked against the configured one."""
        if self._discovery is None:
            found = await self._get(f"{self.settings.issuer}/.well-known/openid-configuration")
            if not isinstance(found, dict):
                raise SignInError("the issuer's discovery document isn't an object")
            if str(found.get("issuer", "")).rstrip("/") != self.settings.issuer:
                raise SignInError("the issuer's discovery document names another issuer")
            needed = ("authorization_endpoint", "token_endpoint", "jwks_uri")
            if not all(isinstance(found.get(k), str) for k in needed):
                raise SignInError("the issuer's discovery document is missing an endpoint")
            self._discovery = found
        return self._discovery

    async def authorize(self, next_path: str) -> tuple[str, Attempt]:
        """Where to send the browser, and the attempt it must bring back."""
        found = await self.discovery()
        attempt = Attempt(
            state=secrets.token_urlsafe(24),
            nonce=secrets.token_urlsafe(24),
            verifier=secrets.token_urlsafe(48),
            next=next_path,
        )
        url = httpx.URL(found["authorization_endpoint"]).copy_merge_params(
            {
                "response_type": "code",
                "client_id": self.settings.client_id,
                "redirect_uri": self.redirect_uri,
                "scope": SCOPES,
                "state": attempt.state,
                "nonce": attempt.nonce,
                "code_challenge": _challenge(attempt.verifier),
                "code_challenge_method": "S256",
            }
        )
        return str(url), attempt

    async def finish(self, code: str, state: str, attempt: Attempt) -> Identity:
        """Trade the callback's *code* for an id token and check it.

        :raises SignInError: The state isn't this attempt's, or the issuer or its token says no.
        """
        if not hmac.compare_digest(state.encode(), attempt.state.encode()):
            raise SignInError("the sign-in's state doesn't match")
        id_token = await self._exchange(code, attempt.verifier)
        return await self.verify(id_token, attempt.nonce)

    async def _exchange(self, code: str, verifier: str) -> str:
        found = await self.discovery()
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": verifier,
        }
        auth: httpx.BasicAuth | None = None
        methods = found.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
        if "client_secret_basic" in methods:
            # RFC 6749 2.3.1: the id and secret are form-encoded before going into the header.
            auth = httpx.BasicAuth(
                quote(self.settings.client_id, safe=""), quote(self.settings.client_secret, safe="")
            )
        else:
            form |= {
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
            }
        try:
            async with self._client() as http:
                response = await http.post(found["token_endpoint"], data=form, auth=auth)
            response.raise_for_status()
            id_token = response.json().get("id_token")
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            raise SignInError(f"the issuer refused the sign-in ({type(exc).__name__})") from None
        if not isinstance(id_token, str):
            raise SignInError("the issuer sent no id token")
        return id_token

    async def _key(self, kid: str | None) -> jwt.PyJWK:
        for fresh in (False, True):
            if self._keys is None or fresh:
                found = await self.discovery()
                try:
                    self._keys = jwt.PyJWKSet.from_dict(await self._get(found["jwks_uri"]))
                except (jwt.PyJWTError, AttributeError, TypeError):
                    raise SignInError("the issuer's keys can't be read") from None
            keys = self._keys.keys
            if kid is None and len(keys) == 1:
                return keys[0]
            for key in keys:
                if kid is not None and key.key_id == kid:
                    return key
        raise SignInError("the id token is signed with a key the issuer doesn't publish")

    async def verify(self, id_token: str, nonce: str) -> Identity:
        """Who *id_token* says signed in, once everything about it checks out.

        :raises SignInError: Anything doesn't.
        """
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError:
            raise SignInError("the id token can't be read") from None
        alg = header.get("alg")
        if alg not in ALGORITHMS:
            raise SignInError("the id token isn't signed with a public key")
        key = await self._key(header.get("kid"))
        issuer = str((await self.discovery())["issuer"])
        try:
            claims = jwt.decode(
                id_token,
                key=key.key,
                algorithms=[alg],
                audience=self.settings.client_id,
                issuer=issuer,
                leeway=LEEWAY,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise SignInError(f"the id token was refused ({type(exc).__name__})") from None
        audience = claims["aud"]
        many = isinstance(audience, list) and len(audience) > 1
        if many and claims.get("azp") != self.settings.client_id:
            raise SignInError("the id token was given to another client")
        got = claims.get("nonce")
        if not isinstance(got, str) or not hmac.compare_digest(got.encode(), nonce.encode()):
            raise SignInError("the id token's nonce doesn't match")
        email = claims.get("email") if isinstance(claims.get("email"), str) else ""
        name = claims.get("name") if isinstance(claims.get("name"), str) else ""
        return Identity(
            issuer=self.settings.issuer, subject=str(claims["sub"]), email=email, name=name
        )
