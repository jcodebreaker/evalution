"""Hosted sign-in, workspaces, invites, API tokens and each person's model key, against a fake
OpenID Connect issuer (no network)."""

import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
import yaml
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from evalution import adapters, cli, remote
from evalution.accounts import keys, people
from evalution.accounts.oidc import Issuer
from evalution.accounts.settings import (
    CLIENT_ID_ENV,
    CLIENT_SECRET_ENV,
    ISSUER_ENV,
    PUBLIC_URL_ENV,
)
from evalution.adapters.base import Capabilities, Isolation, Session, Target, Turn
from evalution.api.app import create_app
from evalution.api.auth import LOGIN_COOKIE, safe_next
from evalution.api.runs import UPLOAD_BYTES
from evalution.config import load, save_page_target
from evalution.results.store import RunStore
from evalution.storage import Database
from evalution.storage.secrets import KEY_ENV
from evalution.suite.store import put_case, save_set

ISSUER = "https://issuer.test"
CLIENT = "evals-client"
CSRF = {"X-Requested-With": "evalution"}
KEY = "sk-test-0123456789abcdWXYZ"


class FakeIssuer:
    """Discovery, keys and a token endpoint; the id token's claims and signing can be spoiled."""

    def __init__(self):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.subject, self.name, self.email = "ana-1", "Ana", "ana@example.com"
        self.nonce = ""
        self.spoil = {}
        self.sign = None

    def id_token(self):
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT,
            "sub": self.subject,
            "email": self.email,
            "name": self.name,
            "nonce": self.nonce,
            "iat": now,
            "exp": now + 300,
        } | self.spoil
        if self.sign:
            return self.sign(claims)
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "k1"})

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                },
            )
        if path == "/jwks":
            jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
            return httpx.Response(200, json={"keys": [jwk | {"kid": "k1", "use": "sig"}]})
        if path == "/token":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["authorization_code"]
            assert form["code_verifier"][0]
            assert request.headers["authorization"].startswith("Basic ")
            return httpx.Response(200, json={"id_token": self.id_token()})
        return httpx.Response(404)


@pytest.fixture
def issuer():
    return FakeIssuer()


@pytest.fixture
def app(tmp_path, monkeypatch, issuer):
    for name, value in {
        ISSUER_ENV: ISSUER,
        CLIENT_ID_ENV: CLIENT,
        CLIENT_SECRET_ENV: "not-a-real-secret",
        PUBLIC_URL_ENV: "https://evals.test",
        KEY_ENV: Fernet.generate_key().decode(),
    }.items():
        monkeypatch.setenv(name, value)
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump({"targets": {"bot": {"adapter": "http", "http": {"send": {"url": "x"}}}}})
    )
    made = create_app(load(tmp_path / "evalution.yaml"))
    made.state.issuer = Issuer(made.state.auth, transport=httpx.MockTransport(issuer.handle))
    return made


def browser(app):
    return TestClient(app, base_url="https://testserver")


def sign_in(app, issuer, subject="ana-1", name="Ana", next_path="/runs"):
    """A browser signed in as *subject*, and the callback's response."""
    client = browser(app)
    issuer.subject, issuer.name, issuer.email = subject, name, f"{subject}@example.com"
    out = client.get("/auth/login", params={"next": next_path}, follow_redirects=False)
    assert out.status_code == 302
    query = parse_qs(urlsplit(out.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["https://evals.test/auth/callback"]
    issuer.nonce = query["nonce"][0]
    back = client.get(
        "/auth/callback",
        params={"code": "the-code", "state": query["state"][0]},
        follow_redirects=False,
    )
    return client, back


def test_first_sign_in_makes_a_personal_workspace(app, issuer):
    client, back = sign_in(app, issuer)
    assert back.status_code == 302 and back.headers["location"] == "/runs"
    cookie = next(c for c in back.headers.get_list("set-cookie") if c.startswith(LOGIN_COOKIE))
    assert "HttpOnly" in cookie and "Secure" in cookie and "samesite=lax" in cookie.lower()
    me = client.get("/v1/me").json()
    assert me["hosted"] and me["user"]["name"] == "Ana"
    assert me["workspace"]["name"] == "Ana" and me["workspace"]["personal"]
    assert me["workspaces"] == [me["workspace"]]
    assert me["model_key"] == {"set": False, "last4": None}

    again, _ = sign_in(app, issuer)
    assert again.get("/v1/me").json()["workspaces"] == [me["workspace"]]

    assert client.post("/auth/logout", headers=CSRF).status_code == 204
    assert client.get("/v1/me").json()["user"] is None
    assert again.get("/v1/me").json()["user"]["name"] == "Ana"


def test_signed_out(app):
    client = browser(app)
    me = client.get("/v1/me").json()
    assert me == {
        "hosted": True,
        "user": None,
        "workspace": None,
        "workspaces": [],
        "model_key": {"set": False, "last4": None},
    }
    assert client.get("/v1/health").status_code == 200
    assert client.get("/v1/targets").json() == {"detail": "sign in"}
    assert client.get("/v1/targets").status_code == 401


def _hs256(claims):
    return jwt.encode(claims, "shared-secret-shared-secret-shared", algorithm="HS256")


def _none(claims):
    return jwt.encode(claims, None, algorithm="none")


def _other_key(claims):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return jwt.encode(claims, other, algorithm="RS256", headers={"kid": "k1"})


@pytest.mark.parametrize(
    ("spoil", "sign"),
    [
        ({"nonce": "another"}, None),
        ({"aud": "another-client"}, None),
        ({"iss": "https://elsewhere.test"}, None),
        ({"exp": int(time.time()) - 3600}, None),
        ({}, _hs256),
        ({}, _none),
        ({}, _other_key),
    ],
)
def test_a_bad_id_token_signs_no_one_in(app, issuer, spoil, sign):
    issuer.spoil, issuer.sign = spoil, sign
    client, back = sign_in(app, issuer)
    assert back.status_code == 400
    assert LOGIN_COOKIE not in back.headers.get("set-cookie", "")
    assert client.get("/v1/me").json()["user"] is None


def test_the_callback_needs_the_browser_that_started(app, issuer):
    _, back = sign_in(app, issuer)
    assert back.status_code == 302
    stranger = browser(app)
    out = stranger.get("/auth/callback", params={"code": "c", "state": "s"})
    assert out.status_code == 400


@pytest.mark.parametrize(
    ("given", "lands"),
    [
        ("/runs?set=main", "/runs?set=main"),
        ("//evil.test/x", "/"),
        ("https://evil.test", "/"),
        ("/\\evil.test", "/"),
        ("/\t/evil.test", "/"),
        ("runs", "/"),
    ],
)
def test_next_stays_on_this_server(given, lands):
    assert safe_next(given) == lands


def test_open_redirect_is_refused_at_the_callback(app, issuer):
    _, back = sign_in(app, issuer, next_path="//evil.test/steal")
    assert back.headers["location"] == "/"


def test_writes_signed_in_by_cookie_need_the_header(app, issuer):
    client, _ = sign_in(app, issuer)
    assert client.post("/v1/workspaces", json={"name": "Team"}).status_code == 403
    made = client.post("/v1/workspaces", json={"name": "Team"}, headers=CSRF)
    assert made.status_code == 200 and made.json()["personal"] is False


class Answers(Target):
    """An app that only runs on this computer: it answers every message."""

    name = "bot"
    capabilities = Capabilities(Isolation.NONE, "")

    async def open(self, run):
        return Session(run, "s", run)

    async def send(self, session, message, *, files=(), stop_after=None, deadline):
        return Turn("hello", time.time(), time.time())

    async def close(self, session):
        return None


def _play_here_and_upload(app, tmp_path, monkeypatch, token, team):
    """``evalution run --server``: the team's tests, played by an app only this computer reaches,
    and the run uploaded to the team. The in-process app stands in for the server."""
    docs = app.state.accounts.docs(team)
    save_page_target(docs, "bot", {"adapter": "http", "http": {"send": {"url": "x"}}})
    save_set(docs, "bot", "main", {"name": "Main"})
    put_case(docs, "bot", "main", "greets", {"name": "Greets", "turns": [{"message": "hi"}]})
    here = tmp_path / "here"
    here.mkdir()
    (here / "evalution.yaml").write_text(
        yaml.safe_dump({"targets": {"bot": {"adapter": "http", "http": {"send": {"url": "y"}}}}})
    )
    monkeypatch.setitem(adapters._built, "bot", Answers())
    monkeypatch.setenv(remote.TOKEN_ENV, token)
    monkeypatch.setattr(
        remote,
        "connect",
        lambda server, token: TestClient(
            app, base_url=f"{server}/v1", headers={"Authorization": f"Bearer {token}"}
        ),
    )
    play = ["run", "--target", "bot", "--set", "main", "--tries", "1", "--yes"]
    where = ["--server", "https://testserver", "--env", "laptop"]
    config = here / "evalution.yaml"
    assert cli.main(["--config", str(config), *play, *where]) == 1, "no one could judge it"
    [run] = RunStore(Database.open(load(config).data_dir).docs()).all()
    return run


def test_a_token_reaches_its_own_workspace_only(app, issuer, tmp_path, monkeypatch, capsys):
    ana, _ = sign_in(app, issuer)
    personal = ana.get("/v1/me").json()["workspace"]["id"]
    team = ana.post("/v1/workspaces", json={"name": "Team"}, headers=CSRF).json()["id"]
    made = ana.post("/v1/tokens", json={"name": "ci"}, headers=CSRF | {"X-Workspace": team}).json()
    assert made["token"].startswith("evt_") and made["token"].startswith(made["prefix"])
    listed = ana.get("/v1/tokens", headers={"X-Workspace": team}).json()
    assert [t["prefix"] for t in listed] == [made["prefix"]] and "token" not in listed[0]
    assert ana.get("/v1/tokens").json() == []

    ci = browser(app)
    bearer = {"Authorization": f"Bearer {made['token']}", "X-Workspace": personal}
    me = ci.get("/v1/me", headers=bearer).json()
    assert me["workspace"]["id"] == team and me["user"] is None
    # No header needed, and a token can't make more of itself.
    assert ci.post("/v1/tokens", json={"name": "more"}, headers=bearer).status_code == 403
    assert ci.get("/v1/me", headers={"Authorization": "Bearer evt_nope"}).status_code == 401

    # A run played where the app runs lands in the token's workspace, as played elsewhere.
    played = _play_here_and_upload(app, tmp_path, monkeypatch, made["token"], team)
    out = capsys.readouterr().out
    assert "took 1 set(s)" in out and "uploaded run" in out and made["token"] not in out
    [stored] = ci.get("/v1/evals/runs", headers=bearer).json()["data"]
    assert stored["run"] == played["run"] and stored["environment"] == played["environment"]
    assert played["environment"] == {"where": "local", "name": "laptop"}
    shown = ci.get(f"/v1/evals/runs/{played['run']}/cases/greets", headers=bearer)
    assert shown.status_code == 200 and len(shown.json()["tries"]) == 1
    workspace = app.state.workspaces.get(team)
    assert len(workspace.runs.store.evidence(played["run"])) == 1
    assert ana.get("/v1/evals/runs").json()["data"] == [], "not in Ana's personal workspace"
    # Sent again it replaces itself, and the server, not the sender, says it wasn't played here.
    url = "/v1/evals/runs/import"
    hosted = {**played, "environment": {"where": "hosted", "name": "laptop"}}
    again = ci.post(url, json={"run": hosted, "evidence": []}, headers=bearer)
    assert again.status_code == 200 and again.json()["environment"]["where"] == "local"
    assert len(ci.get("/v1/evals/runs", headers=bearer).json()["data"]) == 1
    assert workspace.runs.store.evidence(played["run"]) == []
    for doc, status, said in (
        ({**played, "target": "nope"}, 404, "create the app in the workspace first"),
        ({**played, "set": "nope"}, 404, "Test set not found"),
        ({**played, "status": "running"}, 400, "run.status"),
    ):
        refused = ci.post(url, json={"run": doc}, headers=bearer)
        assert refused.status_code == status and said in refused.json()["detail"]
    too_big = ci.post(url, content=b" " * (UPLOAD_BYTES + 1), headers=bearer)
    assert too_big.status_code == 413
    assert browser(app).post(url, json={"run": played}).status_code == 401

    gone = ana.delete(f"/v1/tokens/{made['id']}", headers=CSRF | {"X-Workspace": team})
    assert gone.status_code == 204
    assert ci.get("/v1/targets", headers=bearer).status_code == 401


def test_invites_and_leaving(app, issuer):
    ana, _ = sign_in(app, issuer)
    personal = ana.get("/v1/me").json()["workspace"]["id"]
    team = ana.post("/v1/workspaces", json={"name": "Team"}, headers=CSRF).json()["id"]
    bob, _ = sign_in(app, issuer, subject="bob-1", name="Bob")
    bob_id = bob.get("/v1/me").json()["user"]["id"]

    assert bob.get("/v1/me", headers={"X-Workspace": team}).status_code == 404
    assert bob.get(f"/v1/workspaces/{team}/members").status_code == 404
    assert ana.post(f"/v1/workspaces/{personal}/invites", headers=CSRF).status_code == 400

    url = ana.post(f"/v1/workspaces/{team}/invites", headers=CSRF).json()["url"]
    assert url.startswith("https://evals.test/invite/")
    code = url.rsplit("/", 1)[1]
    joined = bob.post(f"/v1/invites/{code}/accept", headers=CSRF).json()
    assert joined["workspace"] == {"id": team, "name": "Team", "personal": False}
    assert bob.post("/v1/invites/nope/accept", headers=CSRF).status_code == 404
    names = [m["name"] for m in bob.get(f"/v1/workspaces/{team}/members").json()]
    assert names == ["Ana", "Bob"]
    renamed = bob.patch(f"/v1/workspaces/{team}", json={"name": "Crew"}, headers=CSRF)
    assert renamed.json()["name"] == "Crew"

    leave = bob.delete(f"/v1/workspaces/{team}/members/{bob_id}", headers=CSRF)
    assert leave.status_code == 204
    assert bob.get("/v1/me", headers={"X-Workspace": team}).status_code == 404
    ana_id = ana.get("/v1/me").json()["user"]["id"]
    last = ana.delete(f"/v1/workspaces/{team}/members/{ana_id}", headers=CSRF)
    assert last.status_code == 400 and last.json()["detail"] == "the last member can't leave"
    own = ana.delete(f"/v1/workspaces/{personal}/members/{ana_id}", headers=CSRF)
    assert own.status_code == 400


def test_each_person_keeps_their_own_model_key(app, issuer):
    ana, _ = sign_in(app, issuer)
    team = ana.post("/v1/workspaces", json={"name": "Team"}, headers=CSRF).json()["id"]
    bob, _ = sign_in(app, issuer, subject="bob-1", name="Bob")
    code = ana.post(f"/v1/workspaces/{team}/invites", headers=CSRF).json()["url"].rsplit("/")[-1]
    bob.post(f"/v1/invites/{code}/accept", headers=CSRF)
    bob_id = bob.get("/v1/me").json()["user"]["id"]
    database = app.state.accounts

    saved = bob.put("/v1/me/model-key", json={"key": KEY}, headers=CSRF)
    assert saved.json() == {"set": True, "last4": "WXYZ"} and KEY not in saved.text
    me = bob.get("/v1/me")
    assert me.json()["model_key"] == {"set": True, "last4": "WXYZ"} and KEY not in me.text
    # Ana, the earliest member, has none: background work falls to Bob's; his own work uses his.
    assert keys.model_key_for(database, team, None) == KEY
    assert keys.model_key_for(database, team, bob_id) == KEY
    ana_personal = people.personal_workspace(database, ana.get("/v1/me").json()["user"]["id"])
    assert keys.model_key_for(database, ana_personal.id, bob_id) is None
    assert keys.model_key_for(database, "local", bob_id) is None

    # Hosted, the settings form doesn't keep a key for the whole workspace.
    form = {"provider": "anthropic", "strong": "s", "light": "l", "key": KEY}
    refused = bob.put("/v1/settings/model", json=form, headers=CSRF)
    assert refused.status_code == 400 and KEY not in refused.text

    assert bob.delete("/v1/me/model-key", headers=CSRF).status_code == 204
    assert bob.get("/v1/me").json()["model_key"] == {"set": False, "last4": None}
    assert keys.model_key_for(database, team, None) is None


def test_hosted_needs_the_secret_key(app, tmp_path, monkeypatch):
    monkeypatch.delenv(KEY_ENV)
    with pytest.raises(RuntimeError, match=KEY_ENV):
        create_app(load(tmp_path / "evalution.yaml"))


def test_local_has_no_login(tmp_path):
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"targets": {}}))
    client = TestClient(create_app(load(tmp_path / "evalution.yaml")))
    me = client.get("/v1/me").json()
    local = {"id": "local", "name": "Local", "personal": True}
    assert me["hosted"] is False and me["user"] is None
    assert me["workspace"] == local and me["workspaces"] == [local]
    assert client.get("/auth/login").status_code != 302
