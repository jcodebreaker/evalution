"""A copy of the app sends its traces to evalution's own intake, so a run on it can be judged:
the settings come from the adapter (or the standard OpenTelemetry ones) and reach only the
services that already read them."""

from types import SimpleNamespace

from evalution.isolation import launch
from evalution.runs import isolated

URL = "http://127.0.0.1:8080"


def test_loopback_becomes_what_a_container_reaches():
    assert launch.for_containers("http://127.0.0.1:8080") == "http://host.docker.internal:8080"
    assert launch.for_containers("http://localhost/v1") == "http://host.docker.internal/v1"
    assert launch.for_containers("http://db:5432") == "http://db:5432"
    assert launch.for_containers("http/protobuf") == "http/protobuf"


def test_trace_settings_reach_only_services_that_read_them():
    config = {
        "services": {
            "engine": {
                "image": "app",
                "environment": {
                    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://host.docker.internal:6006",
                    "OTEL_EXPORTER_OTLP_PROTOCOL": "",
                    "OTHER": "x",
                },
                "ports": [{"target": 8000, "published": "8012"}],
            },
            "postgres": {"image": "postgres:16", "environment": {"POSTGRES_DB": "app"}},
        }
    }
    env = {
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://host.docker.internal:8080",
        "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
        "OTEL_METRICS_EXPORTER": "none",
    }
    out = launch.override_for(config, "eval-app-0", pick=iter([50001]).__next__, env=env)
    assert 'OTEL_EXPORTER_OTLP_ENDPOINT: "http://host.docker.internal:8080"' in out.text
    assert 'OTEL_EXPORTER_OTLP_PROTOCOL: "http/protobuf"' in out.text
    # Not declared by the engine, so not set: the compose file's own wiring decides.
    assert "OTEL_METRICS_EXPORTER" not in out.text
    assert "postgres:\n    environment" not in out.text
    assert "engine.OTEL_EXPORTER_OTLP_ENDPOINT" in out.rewired
    assert "engine.OTEL_EXPORTER_OTLP_ENDPOINT" not in out.fixed


def _config(adapter: str):
    return SimpleNamespace(targets={"app": SimpleNamespace(adapter=adapter)})


def test_trace_env_uses_the_adapters_own_lines(monkeypatch):
    def setup(url):
        return {"lines": f"OTEL_EXPORTER_OTLP_ENDPOINT={url}\nAPP_CAPTURE=true\n# a comment"}

    monkeypatch.setattr(isolated, "infos", lambda: {"mine": SimpleNamespace(trace_setup=setup)})
    assert isolated.trace_env(_config("mine"), "app", URL) == {
        "OTEL_EXPORTER_OTLP_ENDPOINT": URL,
        "APP_CAPTURE": "true",
    }


def test_trace_env_takes_an_adapter_written_for_url_and_builtin(monkeypatch):
    def setup(url, builtin):
        return {"lines": f"OTEL_EXPORTER_OTLP_ENDPOINT={url}\nBUILTIN={builtin}"}

    monkeypatch.setattr(isolated, "infos", lambda: {"old": SimpleNamespace(trace_setup=setup)})
    assert isolated.trace_env(_config("old"), "app", URL)["BUILTIN"] == "True"


def test_trace_env_falls_back_to_standard_lines_naming_the_target(monkeypatch):
    nothing = SimpleNamespace(trace_setup=lambda url: None)
    monkeypatch.setattr(isolated, "infos", lambda: {"plain": nothing})
    env = isolated.trace_env(_config("plain"), "app", URL)
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == URL
    assert env["OTEL_RESOURCE_ATTRIBUTES"] == "evalution.target=app"
    assert env["OTEL_METRICS_EXPORTER"] == "none"


async def test_the_copies_intake_keeps_spans_where_runs_read_them(tmp_path):
    import httpx

    from evalution.storage import Database
    from evalution.traces.builtin import SpanStore
    from tests.test_traces import GENAI, _built_in_spans, _otlp_json

    store = SpanStore(Database.open(tmp_path / "data"), "local")
    intake = await isolated.Intake.start(store)
    try:
        async with httpx.AsyncClient() as client:
            # A container's exporter names this computer as host.docker.internal: no Host check.
            sent = await client.post(
                f"{intake.url}/v1/traces",
                json=_otlp_json(GENAI, {"evalution.target": "app"}),
                headers={"Host": "host.docker.internal"},
            )
        assert sent.status_code == 200
        assert await _built_in_spans(store, "app")
    finally:
        await intake.close()
