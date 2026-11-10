"""Saying no where a reset can't hold (B8): only a Postgres database gets the Postgres driver, a
database that keeps nothing on disk is cleared by a restart, any other is not put back; and on
Linux the copy can reach the trace intake by name. Offline: no Docker."""

from pathlib import Path

from evalution.isolation import isolate as iso
from evalution.isolation.drivers import PostgresStore
from evalution.isolation.launch import HOST_GATEWAY, ComposeCopy, override_for
from evalution.isolation.plan import Plan, PlannedStore, holds_state, in_container, not_put_back
from evalution.runs import isolated


def _store(sid: str, kind: str, locator: str, strategy: str = "relocate") -> PlannedStore:
    return PlannedStore(sid, kind, locator, strategy=strategy)  # type: ignore[arg-type]


PG = _store("postgres-app", "postgres", "eval-bot-postgres-1:app")
REDIS = _store("redis-cache", "redis", "eval-bot-redis-1:0")
MYSQL = _store("mysql-app", "mysql", "eval-bot-mysql-1:app")


class Copy(ComposeCopy):
    def __init__(self, folder: Path) -> None:
        super().__init__([], None, "bot", 0, folder)

    async def services(self) -> dict[str, str]:
        return {
            "eval-bot-0-postgres-1": "postgres",
            "eval-bot-0-redis-1": "redis",
            "eval-bot-0-mysql-1": "mysql",
        }


async def test_only_a_postgres_database_gets_the_postgres_driver(monkeypatch, tmp_path) -> None:
    async def run(*args, **kwargs):
        return 0, "", ""

    monkeypatch.setattr(iso, "_run", run)
    monkeypatch.setattr(iso, "_inspect_env", lambda container: {"POSTGRES_USER": "app"})
    notes: list[str] = []
    plan = Plan(target="bot", stores=[PG, REDIS, MYSQL])
    made = await iso.drivers_for(plan, Copy(tmp_path), notes=notes)
    assert [type(d) for d in made] == [PostgresStore]
    assert made[0].container == "eval-bot-0-postgres-1"  # type: ignore[attr-defined]
    assert notes == [
        "redis-cache: redis, in its container's memory; a restart clears it",
        "mysql-app: a mysql database, not put back (eval-bot-mysql-1:app)",
    ]


def test_a_database_with_no_disk_needs_a_restart_and_another_kind_isnt_put_back() -> None:
    assert holds_state(Plan(target="bot", stores=[PG, REDIS]))
    assert not holds_state(Plan(target="bot", stores=[PG, MYSQL]))
    assert [not_put_back(s) for s in (PG, REDIS, MYSQL)] == [False, False, True]
    assert not_put_back(_store("hosted", "hosted", "env:MEM_API_KEY", "none"))
    assert in_container("eval-bot-redis-1:0") and not in_container("env:DATABASE_URL")
    assert not in_container("volume:data") and not in_container("/srv/app.db")
    # What a run promises clean: a database nothing puts back could hold the memory.
    assert isolated.covered(Plan(target="bot", stores=[PG, REDIS])) == {"conversation", "memory"}
    assert isolated.covered(Plan(target="bot", stores=[PG, MYSQL])) == {"conversation"}


CONFIG = {
    "services": {
        "api": {"environment": {"A": "1"}},
        "worker": {"extra_hosts": ["host.docker.internal=host-gateway"]},
        "beat": {"extra_hosts": {"host.docker.internal": ["host-gateway"]}},
    }
}


def test_on_linux_every_service_reaches_this_computer_by_name() -> None:
    out = override_for(CONFIG, "eval-bot-0", linux=True)
    assert f'  api:\n    extra_hosts:\n      - "{HOST_GATEWAY}"' in out.text
    # Already there: not added again.
    assert "worker" not in out.text and "beat" not in out.text
    assert "extra_hosts" not in override_for(CONFIG, "eval-bot-0", linux=False).text


def test_a_volume_is_kept_in_a_second_volume_inside_docker(monkeypatch, tmp_path) -> None:
    """B5: keep and put back copy between the volume and ``<volume>_golden`` in one container;
    nothing streams through this computer."""
    import pytest

    from evalution.isolation.drivers import Refused, VolumeStore
    from evalution.isolation.drivers import volume as driver

    calls: list[tuple[str, ...]] = []

    def docker(*args: str, **kwargs) -> str:
        assert "stdin" not in kwargs and "stdout" not in kwargs  # no tar through here
        calls.append(args)
        return ""

    monkeypatch.setattr(driver, "docker", docker)
    store = VolumeStore("eval-bot-0_data")
    store.keep(tmp_path / "kept")
    assert calls[1] == ("volume", "create", "--label", "evalution.driver=volume", store.golden)
    assert store.golden == "eval-bot-0_data_golden"
    keep = calls[2]
    assert "eval-bot-0_data:/from:ro" in keep and f"{store.golden}:/v" in keep
    assert "cp -a /from/. /v/" in keep[-1] and keep[-1].startswith("find /v -mindepth 1")
    calls.clear()
    store.put_back(tmp_path / "kept")
    (back,) = calls
    assert f"{store.golden}:/from:ro" in back and "eval-bot-0_data:/v" in back
    calls.clear()
    store.drop_golden(tmp_path / "kept")
    assert calls == [("volume", "rm", "-f", store.golden)]
    # A folder kept from another volume isn't put back over this one.
    with pytest.raises(Refused, match="another volume"):
        VolumeStore("eval-bot-1_data").put_back(tmp_path / "kept")


def test_on_linux_without_the_tag_the_resolved_file_gets_it_too() -> None:
    import json

    whole = json.loads(override_for(CONFIG, "eval-bot-0", linux=True, tag=False).text)
    services = whole["services"]
    assert services["api"]["extra_hosts"] == [HOST_GATEWAY]
    assert services["worker"]["extra_hosts"] == ["host.docker.internal=host-gateway"]
    assert services["beat"]["extra_hosts"] == {"host.docker.internal": ["host-gateway"]}
