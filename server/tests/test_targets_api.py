"""Connecting a target from the page: create, check, edit, delete; secrets never echoed."""

import asyncio
import json
import stat

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from evalution import cli
from evalution.api.app import create_app
from evalution.config import ConfigError, PhoenixConfig, load
from evalution.connect import connect, probe
from evalution.core import net
from evalution.results.store import RunLock
from evalution.runs.preflight import RUN_KEY, AdapterCheck
from evalution.runs.runner import RunBusyError
from evalution.traces.phoenix import Phoenix
from evalution.traces.profile import recent_sessions

PROXY = "proxy-value-123"
PHOENIX_KEY = "phoenix-value-456"


@pytest.fixture
def client(tmp_path):
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump({"targets": {"hand": {"adapter": "http", "http": {"send": {"url": "x"}}}}})
    )
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "README.md").write_text("hi")
    (tmp_path / "app" / ".env").write_text("NOPE=1")
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as made:
        yield made


def _body(**secrets):
    return {
        "name": "shop",
        "adapter": "langgraph",
        # Port 1 refuses at once: the check stays offline.
        "options": {"url": "http://127.0.0.1:1", "assistant_id": "agent"},
        "phoenix": {"url": "http://127.0.0.1:1", "project": "shop"},
        "code": {"path": "app"},
        "secrets": secrets,
    }


def test_create_check_edit_delete(client, tmp_path) -> None:
    made = client.post("/v1/targets", json=_body(api_key=PROXY, phoenix_api_key=PHOENIX_KEY))
    assert made.status_code == 201, made.text
    assert made.json()["secrets"] == ["api_key", "phoenix_api_key"]
    assert made.json()["code"]["ignore"] == []

    stored = tmp_path / "data" / "secrets.json"
    assert stat.S_IMODE(stored.stat().st_mode) == 0o600
    assert json.loads(stored.read_text()) == {
        "shop.api_key": PROXY,
        "shop.phoenix_api_key": PHOENIX_KEY,
    }
    written = (tmp_path / "targets.yaml").read_text()
    assert PROXY not in written and "api_key_env: shop.api_key" in written
    assert (tmp_path / "sets" / "shop" / "main" / "set.json").is_file()

    rows = {r["name"]: r for r in client.get("/v1/targets").json()["data"]}
    assert rows["shop"]["managed"] and not rows["hand"]["managed"]
    for path in ("/v1/targets", "/v1/targets/shop"):
        text = client.get(path).text
        assert PROXY not in text and PHOENIX_KEY not in text

    checked = client.post("/v1/targets/shop/reach").json()
    assert not checked["adapter"]["ok"] and not checked["phoenix"]["ok"]
    assert checked["phoenix"]["sessions_seen"] == 0
    assert checked["code"] == {"ok": True, "detail": "1 files", "files_matched": 1}
    assert PROXY not in json.dumps(checked)

    # A secret left out is kept, null removes it.
    edited = client.put("/v1/targets/shop", json=_body(phoenix_api_key=None))
    assert edited.json()["secrets"] == ["api_key"]
    assert "shop.phoenix_api_key" not in (tmp_path / "targets.yaml").read_text()

    assert client.delete("/v1/targets/hand").status_code == 409
    gone = client.delete("/v1/targets/shop").json()
    assert gone["removed"] and "stay on disk" in gone["kept"]
    assert json.loads(stored.read_text()) == {}
    assert "shop" not in (tmp_path / "targets.yaml").read_text()
    assert (tmp_path / "sets" / "shop" / "main" / "set.json").is_file()
    assert client.get("/v1/targets/shop").status_code == 404


def test_bad_bodies_are_refused_without_echo(client) -> None:
    bad = client.post("/v1/targets", json={**_body(api_key=PROXY), "name": "Bad Name"})
    assert bad.status_code == 400 and PROXY not in bad.text
    listed = client.post("/v1/targets", json=[PROXY])
    assert listed.status_code == 400 and PROXY not in listed.text
    body = _body()
    body["adapter"], body["options"] = "http", {"send": {"url": "http://x"}, "reply": "$.r"}
    assert client.post("/v1/targets", json=body).json()["detail"] == "options.done: required"
    assert client.post("/v1/targets", json={**_body(), "name": "hand"}).status_code == 409
    # No login: a client from another computer is refused.
    with TestClient(client.app, client=("10.0.0.7", 5000)) as remote:
        assert remote.get("/v1/targets").status_code == 403


async def test_sessions_are_found_under_a_flood_of_other_spans() -> None:
    def span(i: int, kind: str, attrs: dict) -> dict:
        return {
            "context": {"span_id": f"s{i}", "trace_id": f"t{i}"},
            "span_kind": kind,
            "start_time": "2026-11-01T00:00:00Z",
            "attributes": attrs,
        }

    flood = [span(i, "UNKNOWN", {"session.id": f"http{i}"}) for i in range(1000)]
    ours = [span(9000 + i, "LLM", {"session.id": f"chat{i}"}) for i in range(3)]

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("cursor") or 0)
        rows = flood if page < 3 else ours
        return httpx.Response(200, json={"data": rows, "next_cursor": str(page + 1)})

    phoenix = Phoenix(PhoenixConfig("http://phoenix"), transport=httpx.MockTransport(handler))
    found = await recent_sessions(phoenix, "session.id", 3, max_pages=10)
    assert sorted(spans[0].attrs["session"]["id"] for spans in found) == [
        "chat0",
        "chat1",
        "chat2",
    ]


MODEL_KEY = "model-value-789"


def test_model_settings_round_trip(client, tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("EVALUTION_MODEL_KEY", raising=False)
    empty = client.get("/v1/settings/model").json()
    assert empty["source"] == "page" and not empty["key_set"] and empty["spent_usd"] == 0

    body = {"provider": "anthropic", "strong": "claude-opus-5-5", "light": "claude-haiku-5-5"}
    saved = client.put("/v1/settings/model", json={**body, "key": MODEL_KEY})
    assert saved.status_code == 200, saved.text
    assert saved.json()["key_set"] and saved.json()["strong"] == "claude-opus-5-5"
    for text in (saved.text, client.get("/v1/settings/model").text):
        assert MODEL_KEY not in text
    assert MODEL_KEY not in (tmp_path / "model.yaml").read_text()
    stored = json.loads((tmp_path / "data" / "secrets.json").read_text())
    assert stored == {"model.api_key": MODEL_KEY}
    # The running server uses it at once, and a key left out is kept.
    assert client.app.state.runs.llm.config.models["light"] == "claude-haiku-5-5"
    moved = client.put(
        "/v1/settings/model",
        json={"provider": "compatible", "base_url": "http://x/v1", "strong": "a", "light": "b"},
    ).json()
    assert moved["provider"] == "compatible" and moved["base_url"] == "http://x/v1"
    # A stored key goes only where it was given for: a new host needs it typed again.
    assert not moved["key_set"] and load(tmp_path / "evalution.yaml").model.api_key() is None
    assert moved["key_dropped"], "and it says so"
    assert not saved.json()["key_dropped"], "control: a key typed, nothing left behind"
    again = client.put(
        "/v1/settings/model",
        json={"provider": "compatible", "base_url": "http://y/v1", "strong": "a", "light": "b"},
    ).json()
    assert not again["key_dropped"], "control: no key to leave behind"
    bad = client.put("/v1/settings/model", json={**body, "provider": "compatible"})
    assert bad.status_code == 400 and "base_url" in bad.text

    # A model in evalution.yaml wins, and the page may not write over it.
    hand = {"kind": "openai", "base_url": "http://h/v1", "models": {"strong": "s", "light": "l"}}
    (tmp_path / "evalution.yaml").write_text(yaml.safe_dump({"model": hand}))
    with TestClient(create_app(load(tmp_path / "evalution.yaml"))) as other:
        assert other.get("/v1/settings/model").json()["source"] == "config"
        assert other.put("/v1/settings/model", json=body).status_code == 409


async def test_model_list_and_test_offline(tmp_path) -> None:
    from dataclasses import replace

    from evalution import settings
    from evalution.config import ModelConfig

    seen = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/v1/models":
            rows = [{"id": "claude-opus-5-5", "display_name": "Opus"}, {"id": "mystery"}]
            return httpx.Response(200, json={"data": rows})
        usage = {"input_tokens": 1_000_000, "output_tokens": 0}
        content = [{"type": "text", "text": "Hi"}]
        return httpx.Response(200, json={"content": content, "usage": usage})

    stub = httpx.MockTransport(answer)
    model = ModelConfig(
        "anthropic", "https://api.test", None, {"strong": "claude-opus-5-5", "light": "mystery"}
    )
    model = replace(model, key=MODEL_KEY)
    found = await settings.list_models(model, transport=stub)
    assert found == [
        {"id": "claude-opus-5-5", "name": "Opus", "input": 4, "output": 20},
        {"id": "mystery", "name": "mystery"},
    ]
    assert seen[0].headers["x-api-key"] == MODEL_KEY

    # The other root is tried only when this one doesn't speak the protocol, and only then said.
    def rooted(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/models":
            return httpx.Response(200, json={"data": [{"id": "m"}]})
        return httpx.Response(404 if request.url.path.endswith("/models") else 200, text="<html>")

    gateway = ModelConfig("openai", "https://gw.test/v1", None, {"strong": "m", "light": "m"})
    moved = await settings.find_models(gateway, "https://gw.test", httpx.MockTransport(rooted))
    assert moved["base_url"] == "https://gw.test" and "note" not in moved
    moved = await settings.find_models(gateway, "https://gw.test/v1", httpx.MockTransport(rooted))
    assert moved["base_url"] == "https://gw.test" and "gw.test" in moved["note"]
    same = await settings.find_models(
        settings.replace(gateway, base_url="https://gw.test"),
        "https://gw.test",
        httpx.MockTransport(rooted),
    )
    assert same == {"data": [{"id": "m", "name": "m"}], "base_url": "https://gw.test"}

    def not_it(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not an API</html>")

    with pytest.raises(settings.SettingsError, match="did not answer with a model list"):
        await settings.find_models(gateway, "https://gw.test/v1", httpx.MockTransport(not_it))

    def refused(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    with pytest.raises(settings.SettingsError, match="The key was refused"):
        await settings.find_models(gateway, "https://gw.test/v1", httpx.MockTransport(refused))

    spend = tmp_path / "spend.json"
    tested = await settings.test(model, spend, transport=stub)
    assert tested["strong"]["ok"] and tested["light"]["ok"]
    assert MODEL_KEY not in json.dumps(tested)
    assert json.loads(spend.read_text()) == {"usd": 4.0}


@pytest.mark.parametrize(
    ("method", "path", "headers", "status"),
    [
        # A page on another site, posting at the owner's server through their browser.
        ("post", "/v1/targets", {"origin": "https://evil.example.test"}, 403),
        ("post", "/v1/targets", {"origin": "null"}, 403),
        ("get", "/v1/targets", {"host": "evil.example.test"}, 403),
        ("get", "/openapi.json", {"host": "evil.example.test"}, 403),
        ("get", "/v1/targets", {"origin": "https://evil.example.test"}, 200),
        # The page itself, here or on the dev server.
        ("post", "/v1/targets/discover", {"origin": "http://localhost:5173"}, 422),
        ("get", "/v1/targets", {"host": "127.0.0.1:8080"}, 200),
        ("get", "/v1/targets", {"host": "[::1]:8080"}, 200),
    ],
)
def test_only_this_computers_own_pages_may_ask(client, method, path, headers, status) -> None:
    assert getattr(client, method)(path, headers=headers).status_code == status


@pytest.mark.parametrize(
    ("bind", "host", "origin", "status"),
    [
        # A page on another site, under a name it pointed at this server: the owner's secrets.
        ("0.0.0.0", "evil.example.test:8080", None, 403),
        ("0.0.0.0", "evil.example.test:8080", "http://evil.example.test:8080", 403),
        ("192.0.2.7", "evil.example.test", None, 403),
        # All addresses take this computer's names, one address only itself.
        ("0.0.0.0", "localhost:8080", None, 200),
        ("0.0.0.0", "127.0.0.1:8080", None, 200),
        ("0.0.0.0", "[::1]:8080", None, 200),
        ("0.0.0.0", "bot.example.test", None, 200),
        ("192.0.2.7", "192.0.2.7:8080", None, 200),
        ("192.0.2.7", "localhost:8080", None, 403),
        ("192.0.2.7", "BOT.example.test", None, 200),
    ],
)
def test_a_server_open_to_others_answers_only_under_its_names(
    tmp_path, monkeypatch, bind, host, origin, status
) -> None:
    monkeypatch.setenv("EVALUTION_ALLOWED_HOSTS", "bot.example.test")
    (tmp_path / "evalution.yaml").write_text("{}")
    app = create_app(load(tmp_path / "evalution.yaml"), remote=True, bind=bind)
    with TestClient(app) as made:
        headers = {"host": host, **({"origin": origin} if origin else {})}
        answer = made.get("/v1/targets", headers=headers)
        assert answer.status_code == status
        if status == 403:
            assert "EVALUTION_ALLOWED_HOSTS" in answer.text


def test_the_allowed_names_and_the_warning_that_says_them(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("EVALUTION_ALLOWED_HOSTS", raising=False)
    config = tmp_path / "evalution.yaml"
    config.write_text(yaml.safe_dump({"allowed_hosts": ["in-the-file.example.test"]}))
    assert load(config).allowed_hosts == ("in-the-file.example.test",)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **k: None)
    assert cli.main(["--config", str(config), "serve", "--host", "0.0.0.0"]) == 0
    said = capsys.readouterr().err
    assert (
        "EVALUTION_ALLOWED_HOSTS" in said
        and "in-the-file.example.test" in said
        and "localhost" in said
    )
    # Control: a server only this computer reaches has nothing to say.
    assert cli.main(["--config", str(config), "serve", "--host", "127.0.0.1"]) == 0
    assert "warning" not in capsys.readouterr().err
    monkeypatch.setenv("EVALUTION_ALLOWED_HOSTS", " a.example.test, b.example.test ,")
    assert load(config).allowed_hosts == ("a.example.test", "b.example.test")
    monkeypatch.delenv("EVALUTION_ALLOWED_HOSTS")
    config.write_text(yaml.safe_dump({"allowed_hosts": "not-a-list"}))
    with pytest.raises(ConfigError, match="allowed_hosts"):
        load(config)


def _http_body(**options):
    return {
        "name": "shop",
        "adapter": "http",
        "options": {
            "send": {"url": "http://shop.example.test/c", "body": {"m": "{message}"}},
            "reply": "$",
            "done": "response",
            **options,
        },
        "phoenix": {"url": "http://traces.example.test:6006", "project": "shop"},
    }


_ELSEWHERE = "http://evil.example.test/x"


@pytest.mark.parametrize(
    ("remote", "bind", "host", "origin", "status"),
    [
        # A proxy on this computer that keeps the public name, on a plain serve.
        (False, None, "evals.example.test", None, 200),
        (False, None, "evals.example.test", "https://evals.example.test", 200),
        # A proxy that puts in its own name, the page writing from the public one.
        (True, "0.0.0.0", "upstream.example.test:8080", "https://evals.example.test", 200),
        (False, None, "localhost:8080", "https://evals.example.test", 200),
        # Names no one gave stay refused, on both.
        (False, None, "evil.example.test", None, 403),
        (False, None, "evals.example.test", "https://evil.example.test", 403),
        (False, None, "localhost:8080", "https://evil.example.test", 403),
        (True, "0.0.0.0", "upstream.example.test:8080", "https://evil.example.test", 403),
        (True, "0.0.0.0", "evil.example.test", "https://evals.example.test", 403),
        # Host that can't be read: refused, and not by falling over.
        (False, None, "[bad", None, 403),
        (True, "0.0.0.0", "[bad", None, 403),
    ],
)
def test_names_it_was_given_work_behind_a_proxy_and_others_stay_refused(
    tmp_path, monkeypatch, remote, bind, host, origin, status
) -> None:
    # One that can't be read gives no name, so a Host that can't be read matches nothing.
    names = "evals.example.test,upstream.example.test,[bad"
    monkeypatch.setenv("EVALUTION_ALLOWED_HOSTS", names)
    (tmp_path / "evalution.yaml").write_text("{}")
    app = create_app(load(tmp_path / "evalution.yaml"), remote=remote, bind=bind)
    with TestClient(app, client=("127.0.0.1", 5000), raise_server_exceptions=False) as made:
        headers = {"host": host, **({"origin": origin} if origin else {})}
        method = made.post if origin else made.get
        path = "/v1/targets/none/reach" if origin else "/v1/targets"
        answer = method(path, headers=headers)
        if status == 403:
            assert answer.status_code == 403 and "EVALUTION_ALLOWED_HOSTS" in answer.text
        else:
            assert answer.status_code != 403, answer.text


@pytest.mark.parametrize(
    "moved",
    [
        {"send": {"url": _ELSEWHERE, "body": {"m": "{message}"}}},
        {"open": {"url": _ELSEWHERE, "id": "$.id"}},
        {"done": {"poll": {"url": _ELSEWHERE, "until": "$.a == 1"}}},
        {"cancel": {"url": _ELSEWHERE}},
        {"health": _ELSEWHERE},
    ],
    ids=["send", "open", "poll", "cancel", "health"],
)
def test_a_stored_secret_doesnt_follow_a_new_host(client, tmp_path, monkeypatch, moved) -> None:
    both = {"authorization": PROXY, "phoenix_api_key": PHOENIX_KEY}
    made = client.post("/v1/targets", json={**_http_body(), "secrets": both})
    assert made.status_code == 201, made.text
    stored = tmp_path / "data" / "secrets.json"

    # Another route or port on the same host is the same app, and keeps it.
    same = _http_body(
        health="http://SHOP.example.test:81/up", cancel={"url": "http://shop.example.test/s"}
    )
    kept = client.put("/v1/targets/shop", json=same).json()
    assert kept["secrets"] == sorted(both) and kept["secrets_dropped"] == []
    assert "headers_env" in (tmp_path / "targets.yaml").read_text()

    # A form tried, not saved, is held to the same: nothing stored would go out with it.
    seen = {}

    async def check(conf, overlay, *_):
        seen.update(options=conf.options, overlay=overlay)
        return {}

    monkeypatch.setattr(connect, "check", check)
    assert client.post("/v1/targets/shop/reach", json=_http_body(**moved)).status_code == 200
    assert "headers_env" not in seen["options"]["http"], "the stored secret would have gone along"
    assert seen["options"]["phoenix"]["api_key_env"], "the traces' host didn't move"
    assert client.post("/v1/targets/shop/reach", json=same).status_code == 200
    assert "headers_env" in seen["options"]["http"]

    # Typed again, it stays, for the new host.
    typed = {**_http_body(**moved), "secrets": {"authorization": "fake-typed-again"}}
    again = client.put("/v1/targets/shop", json=typed).json()
    assert again["secrets"] == sorted(both) and again["secrets_dropped"] == []
    assert json.loads(stored.read_text())["shop.authorization"] == "fake-typed-again"
    back = client.put("/v1/targets/shop", json=same)
    assert back.status_code == 200

    # Left out, it's gone from the target and from the file, and the other one stays.
    gone = client.put("/v1/targets/shop", json=_http_body(**moved)).json()
    assert gone["secrets"] == ["phoenix_api_key"]
    # Each time one stayed behind, it said so: moving the send address back is a new host too.
    assert back.json()["secrets_dropped"] + gone["secrets_dropped"] == ["authorization"]
    assert "headers_env" not in (tmp_path / "targets.yaml").read_text()
    assert "shop.authorization" not in json.loads(stored.read_text())


def test_a_trace_key_doesnt_follow_a_new_trace_host(client, tmp_path) -> None:
    both = {"authorization": PROXY, "phoenix_api_key": PHOENIX_KEY}
    assert client.post("/v1/targets", json={**_http_body(), "secrets": both}).status_code == 201
    body = _http_body()
    body["phoenix"]["url"] = "http://traces.example.test:7006"
    assert client.put("/v1/targets/shop", json=body).json()["secrets"] == sorted(both)
    body["phoenix"]["url"] = _ELSEWHERE
    assert client.put("/v1/targets/shop", json=body).json()["secrets"] == ["authorization"]
    assert PHOENIX_KEY not in (tmp_path / "data" / "secrets.json").read_text()


def test_a_target_doesnt_change_under_its_run(client) -> None:
    assert client.post("/v1/targets", json=_http_body()).status_code == 201
    runs = client.app.state.runs
    runs.busy = lambda: True
    runs._playing = "shop"
    assert client.put("/v1/targets/shop", json=_http_body()).status_code == 409
    assert client.delete("/v1/targets/shop").status_code == 409
    # Control: another target's run stands in nothing's way, nor does a run that ended.
    runs._playing = "other"
    assert client.put("/v1/targets/shop", json=_http_body()).status_code == 200
    del runs.busy
    runs._playing = "shop"
    assert client.delete("/v1/targets/shop").status_code == 200


def test_a_test_message_and_a_run_keep_out_of_each_others_way(
    client, tmp_path, monkeypatch
) -> None:
    assert client.post("/v1/targets", json=_http_body()).status_code == 201
    runs = client.app.state.runs
    keys = []

    async def one(target, source, settle, stuck, key):
        keys.append(key)
        # While it's on its way: no run starts, and this target's settings don't change.
        with pytest.raises(RunBusyError):
            await runs.start("any")
        # On the server's own loop, which the test client's calls would wait on.
        here = httpx.ASGITransport(app=client.app, client=("127.0.0.1", 1))
        async with httpx.AsyncClient(transport=here, base_url="http://127.0.0.1") as near:
            assert (await near.put("/v1/targets/shop", json=_http_body())).status_code == 409
        return AdapterCheck(reply="OK")

    monkeypatch.setattr(probe, "check_adapter", one)

    # A run in another process holds the lock, and this one's guard sees it.
    other = RunLock(tmp_path / "data" / "run.lock")
    assert other.acquire()
    try:
        assert client.post("/v1/targets/shop/check").status_code == 409
    finally:
        other.release()
    assert keys == []

    # Control: with no run going it's sent, and lets go of everything after.
    assert client.post("/v1/targets/shop/check").json()["ok"]
    assert keys == [probe.PLAYS_AS] and probe.PLAYS_AS != RUN_KEY
    assert not runs.probing and not runs.files.lock.held
    with pytest.raises(KeyError):
        asyncio.run(runs.start("any"))
    assert not runs.files.lock.held


def test_addresses_are_explained_in_words(monkeypatch) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(name.lower(), raising=False)
    gone = httpx.ConnectError("x")
    assert net.notes("http://127.0.0.1:3000/chat") == []
    assert "Nothing is answering at 127.0.0.1:3000" in net.explain("http://127.0.0.1:3000", gone)
    assert "Use 127.0.0.1" in net.notes("http://0.0.0.0:3000")[0]
    # Never a password written into the address.
    assert "hunter2" not in net.explain("https://me:hunter2@far.example/x", gone)

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:3128")
    assert net.proxies_set() == ["HTTP_PROXY", "HTTPS_PROXY"]
    # This machine never goes through it; a remote service does and that is fine.
    assert net.notes("http://127.0.0.1:3000") == []
    assert net.notes("https://far.example/v1") == []
    assert "proxy in HTTPS_PROXY" in net.explain("https://far.example/v1", gone)
    assert "NO_PROXY" in net.explain("https://far.example/v1", httpx.ProxyError("x"))
    assert "Add 10.0.0.5 to NO_PROXY" in net.notes("http://10.0.0.5:3000")[0]
    # Control: once it is in NO_PROXY, nothing to say.
    monkeypatch.setenv("NO_PROXY", "10.0.0.5")
    assert net.notes("http://10.0.0.5:3000") == []

    monkeypatch.setattr(net, "in_container", lambda: True)
    assert "host.docker.internal" in net.notes("http://localhost:3000")[0]
    assert net.notes("http://host.docker.internal:3000") == []


def test_the_test_message_is_remembered_until_the_settings_change(client, monkeypatch) -> None:
    assert client.post("/v1/targets", json=_body(api_key=PROXY)).status_code == 201
    sent = []

    async def one(target, source, settle, stuck, key, *, answer):
        sent.append(target.name)
        return answer

    down = AdapterCheck(["Couldn't open a session: boom " + PROXY], app_down=True)
    monkeypatch.setattr(probe, "check_adapter", lambda *a: one(*a, answer=down))
    failed = client.post("/v1/targets/shop/check")
    assert failed.status_code == 200 and not failed.json()["ok"] and sent == ["shop"]
    assert PROXY not in failed.text and failed.json()["next"]
    assert not failed.json()["can_redraft"], "this adapter's settings aren't the model's to draft"
    row = {r["name"]: r for r in client.get("/v1/targets").json()["data"]}["shop"]
    assert not row["checked"]

    # The free check sends nothing.
    client.post("/v1/targets/shop/reach")
    assert sent == ["shop"]

    good = AdapterCheck(reply="OK", seconds=1.26, tools=1, spans=4, traces="complete")
    monkeypatch.setattr(probe, "check_adapter", lambda *a: one(*a, answer=good))
    passed = client.post("/v1/targets/shop/check").json()
    assert passed["ok"] and passed["reply"] == "OK" and passed["traces"] == "complete"
    assert passed["seconds"] == 1.3 and passed["next"] is None and passed["warnings"] == []
    # No traces: it still works, and is remembered, and says what it can't judge without them.
    plain = AdapterCheck(reply="OK", traces="empty", warnings=["No traces arrived " + PROXY])
    monkeypatch.setattr(probe, "check_adapter", lambda *a: one(*a, answer=plain))
    warned = client.post("/v1/targets/shop/check").json()
    assert warned["ok"] and warned["problems"] == [] and warned["next"]
    assert "No traces arrived" in warned["warnings"][0] and PROXY not in str(warned["warnings"])
    row = {r["name"]: r for r in client.get("/v1/targets").json()["data"]}["shop"]
    assert row["checked"]

    # Other settings are another setup; so a type change is on record too.
    moved = _body()
    moved["options"]["url"] = "http://127.0.0.1:2"
    assert client.put("/v1/targets/shop", json=moved).status_code == 200
    row = {r["name"]: r for r in client.get("/v1/targets").json()["data"]}["shop"]
    assert not row["checked"]
    assert client.get("/v1/targets/shop").json()["adapter_change"] is None
    http = {
        "name": "shop",
        "adapter": "http",
        "options": {"send": {"url": "http://127.0.0.1:1/c"}, "reply": "$", "done": "response"},
    }
    changed = client.put("/v1/targets/shop", json=http).json()
    assert changed["adapter_label"] == "HTTP adapter"
    assert changed["adapter_change"]["from"] == "langgraph"
    assert changed["adapter_change"]["to"] == "http"

    # Nothing to correct until a message failed on these settings; and one app at a time.
    assert client.post("/v1/targets/hand/check/redraft").status_code == 409
    assert client.post("/v1/targets/nope/check").status_code == 404


def test_required_isolation_is_what_the_app_can_promise(client) -> None:
    http = {
        "name": "plain",
        "adapter": "http",
        "options": {"send": {"url": "http://127.0.0.1:1/c"}, "reply": "$", "done": "response"},
    }
    assert client.post("/v1/targets", json=http).status_code == 201
    row = {r["name"]: r for r in client.get("/v1/targets").json()["data"]}["plain"]
    assert row["starts_clean"] == ["conversation"] and row["required_clean"] == []
    assert "new session id" in row["isolation_note"]
    url = "/v1/targets/plain/isolation"
    assert client.put(url, json={"required": ["conversation"]}).status_code == 200
    refused = client.put(url, json={"required": ["memory"]})
    assert refused.status_code == 400 and "can't promise" in refused.json()["detail"]
    assert client.put(url, json={"required": ["nonsense"]}).status_code == 400
    row = {r["name"]: r for r in client.get("/v1/targets").json()["data"]}["plain"]
    assert row["required_clean"] == ["conversation"]
    names = [a["name"] for a in client.get("/v1/adapters").json()["data"]]
    assert "http" in names
