"""What leaves evalution's copy of the app (B7): settings that reach an outside service start
empty on the copy unless the person allowed them, listed by name and never by value. Offline:
no copy is started."""

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from evalution.api.app import create_app
from evalution.config import load
from evalution.isolation import isolate as iso
from evalution.isolation import outbound
from evalution.isolation.launch import ComposeCopy, override_for
from evalution.isolation.plan import Plan, PlannedStore, PlanStore
from evalution.runs import isolated

# Built at run time so no key-shaped literal sits in the repository: it is made up.
LIVE = "sk_" + "live_" + "x" * 28
MAIL = "hunter2-smtp-password"


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("STRIPE_SECRET_KEY", "credential"),
        ("PAYMENT_API_KEY", "payment"),
        ("SMTP_PASSWORD", "mail"),
        ("SMTP_HOST", "mail"),
        ("SMS_AUTH_TOKEN", "sms"),
        ("SLACK_WEBHOOK_URL", "webhook"),
        ("AWS_ACCESS_KEY_ID", "storage"),
        ("SOME_VENDOR_TOKEN", "credential"),
        # The model provider's, evalution's and the app's own: never blocked.
        ("OPENAI_API_KEY", None),
        ("ANTHROPIC_API_KEY", None),
        ("LLM_API_KEY", None),
        ("OTEL_EXPORTER_OTLP_HEADERS", None),
        ("EVALUTION_TOKEN", None),
        ("SECRET_KEY", None),
        ("JWT_SECRET", None),
        ("SESSION_SECRET", None),
        ("POSTGRES_PASSWORD", None),
        ("DATABASE_URL", None),
        # Words of a kind that reach nothing on their own.
        ("EMAIL_FROM", None),
        ("STORAGE_PATH", None),
        ("PORT", None),
    ],
)
def test_what_looks_like_it_reaches_outside(name: str, kind: str | None) -> None:
    assert outbound.kind_of(name) == kind


def _compose_app(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "compose.yaml").write_text(
        yaml.safe_dump(
            {
                "services": {
                    "api": {
                        "image": "app:latest",
                        "env_file": [".env"],
                        "environment": {"SMTP_PASSWORD": "${SMTP_PASSWORD}", "PORT": "8000"},
                        "ports": ["8000:8000"],
                    }
                }
            }
        )
    )
    (folder / ".env").write_text(
        f"STRIPE_SECRET_KEY={LIVE}\nexport SMTP_PASSWORD='{MAIL}'\nOPENAI_API_KEY=sk-model\n"
        "# a comment\nDEBUG=1\n"
    )
    return folder


def test_a_compose_apps_stripe_key_starts_its_copy_empty(tmp_path) -> None:
    folder = _compose_app(tmp_path / "app")
    spec = iso.launch_spec(folder, {})
    found = outbound.found(spec)
    assert [(o.name, o.kind) for o in found] == [
        ("SMTP_PASSWORD", "mail"),
        ("STRIPE_SECRET_KEY", "credential"),
    ]
    shut = outbound.blocked(spec, set())
    assert shut == {"SMTP_PASSWORD": "", "STRIPE_SECRET_KEY": ""}
    # What compose resolves (the env file's values in each service's environment), overridden.
    config = {
        "services": {
            "api": {
                "environment": {"STRIPE_SECRET_KEY": LIVE, "SMTP_PASSWORD": MAIL, "PORT": "8000"},
                "ports": [{"target": 8000, "published": "8000"}],
            }
        }
    }
    made = override_for(config, "eval-bot-0", pick=lambda: 5555, env=shut)
    assert '      STRIPE_SECRET_KEY: ""' in made.text and '      SMTP_PASSWORD: ""' in made.text
    assert LIVE not in made.text and MAIL not in made.text
    # And as compose reads the env file, the copy's own settings win.
    copy = ComposeCopy(
        [folder / "compose.yaml"], folder / ".env", "bot", 0, tmp_path / "w", env=shut
    )
    assert copy.env == shut


def test_an_allowed_setting_keeps_its_value(tmp_path) -> None:
    spec = iso.launch_spec(_compose_app(tmp_path / "app"), {})
    store = outbound.OutboundStore(tmp_path / "data")
    assert store.allow("bot", "STRIPE_SECRET_KEY", True) == {"STRIPE_SECRET_KEY"}
    assert outbound.blocked_for(tmp_path / "data", "bot", spec) == {"SMTP_PASSWORD": ""}
    store.allow("bot", "STRIPE_SECRET_KEY", False)
    assert "STRIPE_SECRET_KEY" in outbound.blocked_for(tmp_path / "data", "bot", spec)
    with pytest.raises(ValueError, match="setting's name"):
        store.allow("bot", "NOT A NAME", True)


def test_a_process_apps_own_settings_are_listed_too() -> None:
    spec = {"kind": "process", "command": ["app"], "env": {"MAILER_API_KEY": "x", "PORT": "1"}}
    assert outbound.blocked(spec, set()) == {"MAILER_API_KEY": ""}


# Wired into the copies ---------------------------------------------------------------------------


@pytest.fixture
def config(tmp_path):
    _compose_app(tmp_path / "app")
    (tmp_path / "evalution.yaml").write_text(
        yaml.safe_dump(
            {"targets": {"bot": {"adapter": "http", "http": {}, "code": {"path": "app"}}}}
        )
    )
    return load(tmp_path / "evalution.yaml")


async def test_a_runs_copies_start_with_what_leaves_them_empty(config, monkeypatch, tmp_path):
    started: list[dict[str, str]] = []

    class Copy:
        url = "http://127.0.0.1:9"

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

    def copy_of(spec, target, slot, folder, *, env=None):
        started.append(dict(env or {}))
        return Copy()

    async def stores_on(*a: Any, **k: Any) -> list[Any]:
        return []

    monkeypatch.setattr(isolated, "copy_of", copy_of)
    monkeypatch.setattr(isolated, "stores_on", stores_on)
    monkeypatch.setattr(isolated, "adapter_on", lambda *a, **k: object())
    monkeypatch.setattr(isolated, "work_folder", lambda *a: tmp_path / "work")
    outbound.OutboundStore(config.data_dir).allow("bot", "SMTP_PASSWORD", True)
    plan = Plan(target="bot", status="proven")
    copies = isolated.Copies(config, "bot", isolated.Footing("proven", plan, 1))
    copies.version_env = {"IMAGE": "app:abc"}
    await copies.start(1)
    assert started == [{"STRIPE_SECRET_KEY": "", "IMAGE": "app:abc"}]


# The API --------------------------------------------------------------------------------------


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as made:
        yield made


def test_the_api_lists_what_leaves_the_copy_and_takes_a_change(client, config) -> None:
    got = client.get("/v1/targets/bot/isolation")
    assert got.status_code == 200
    body = got.json()
    assert body["outbound"] == [
        {"name": "SMTP_PASSWORD", "kind": "mail", "allowed": False},
        {"name": "STRIPE_SECRET_KEY", "kind": "credential", "allowed": False},
    ]
    assert body["put_back"] is None  # no plan yet
    assert LIVE not in got.text and MAIL not in got.text

    put = client.put(
        "/v1/targets/bot/isolation/outbound", json={"name": "STRIPE_SECRET_KEY", "allowed": True}
    )
    assert put.status_code == 200
    assert {o["name"]: o["allowed"] for o in put.json()["outbound"]} == {
        "SMTP_PASSWORD": False,
        "STRIPE_SECRET_KEY": True,
    }
    assert LIVE not in put.text
    bad = client.put("/v1/targets/bot/isolation/outbound", json={"name": "PORT", "allowed": True})
    assert bad.status_code == 400
    bad = client.put("/v1/targets/bot/isolation/outbound", json={"name": "SMTP_PASSWORD"})
    assert bad.status_code == 400


def test_the_api_says_how_a_run_puts_the_copy_back(client, config) -> None:
    store = PlanStore(config.data_dir)
    db = PlannedStore("postgres-app", "postgres", "eval-bot-postgres-1:app", strategy="relocate")
    store.save(Plan(target="bot", stores=[db], status="proven", hot="proven"))
    said = client.get("/v1/targets/bot/isolation").json()["put_back"]
    assert said == {"hot": "proven", "app_holds_state": False, "restart": False}
    store.save(Plan(target="bot", stores=[db], status="proven"))
    said = client.get("/v1/targets/bot/isolation").json()["put_back"]
    assert said == {"hot": None, "app_holds_state": False, "restart": True}


def test_the_api_names_what_the_copy_shares_with_the_app(client, config) -> None:
    assert client.get("/v1/targets/bot/isolation").json()["shared"] == []  # no plan yet
    shared = [
        {"path": "/home/me/app/src", "service": "web", "writable": True},
        {"path": "/home/me/app/config", "service": "web", "writable": False},
    ]
    PlanStore(config.data_dir).save(Plan(target="bot", status="proven", shared=shared))
    assert client.get("/v1/targets/bot/isolation").json()["shared"] == shared
