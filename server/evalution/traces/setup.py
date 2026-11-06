"""How to point an app at evalution's own traces (docs/architecture.md, "Traces").

evalution can't restart someone's app, so it says what to change: :func:`detect` reads the
project's package files and code for OpenInference / OpenTelemetry tracing, and :func:`advice`
turns that into the exact lines to add, in plain words. An adapter that knows better says so
itself (its ``trace_setup``).

* **Traced with Phoenix's** ``register()`` (Python): ``PHOENIX_COLLECTOR_ENDPOINT`` and
  ``PHOENIX_PROJECT_NAME``, which the intake reads as the target.
* **Traced with OpenTelemetry / OpenInference** otherwise: the standard ``OTEL_*`` lines, the
  target as the ``evalution.target`` resource attribute, metrics and logs off.
* **Not traced**: tests still play and are graded on replies, but steps and context need
  tracing; the plain OpenTelemetry SDK exporting OTLP, for its language.
"""

from __future__ import annotations

import re
from typing import Any

from evalution.adapters.base import PY_DEPS
from evalution.traces.builtin import TARGET_ATTRIBUTE, TARGET_PREFIX

#: LLM libraries an OpenInference instrumentation exists for: (import or package word, name).
FRAMEWORKS = (
    ("openai-agents", "openai-agents"),
    ("langchain", "langchain"),
    ("llama_index", "llama-index"),
    ("llama-index", "llama-index"),
    ("litellm", "litellm"),
    ("anthropic", "anthropic"),
    ("openai", "openai"),
)
JS_CLASSES = {"openai": "OpenAIInstrumentation", "langchain": "LangChainInstrumentation"}
#: An instrumentation package's module and class, for the Python snippet without Phoenix.
PY_INSTRUMENTORS = {
    "openai-agents": ("openai_agents", "OpenAIAgentsInstrumentor"),
    "langchain": ("langchain", "LangChainInstrumentor"),
    "llama-index": ("llama_index", "LlamaIndexInstrumentor"),
    "litellm": ("litellm", "LiteLLMInstrumentor"),
    "anthropic": ("anthropic", "AnthropicInstrumentor"),
    "openai": ("openai", "OpenAIInstrumentor"),
}


def _deps(files: dict[str, str]) -> tuple[str, str]:
    """``(python package text, js package text)``, lowercased."""
    py = "\n".join(t for r, t in files.items() if r.rsplit("/", 1)[-1].lower() in PY_DEPS)
    js = "\n".join(t for r, t in files.items() if r.rsplit("/", 1)[-1] == "package.json")
    return py.lower(), js.lower()


def detect(files: dict[str, str]) -> dict[str, Any]:
    """What tracing a project has: ``language`` (``python``, ``js`` or ``None``), ``register``
    (Phoenix's Python ``register()``), ``otel`` (OpenTelemetry or OpenInference set up some
    other way), ``project`` (a project name fixed in code) and ``framework`` (its LLM library,
    for the snippet)."""
    py_deps, js_deps = _deps(files)
    py_code = "\n".join(t for r, t in files.items() if r.endswith(".py"))
    js_code = "\n".join(t for r, t in files.items() if re.search(r"\.[mc]?[jt]sx?$", r))
    if js_deps and not py_deps:
        language = "js"
    elif py_deps or py_code:
        language = "python"
    else:
        language = "js" if js_code else None

    register = bool(re.search(r"phoenix\.otel|from phoenix import otel", py_code)) and bool(
        re.search(r"\bregister\(", py_code)
    )
    otel = bool(
        re.search(r"openinference|opentelemetry", py_deps + js_deps)
        or re.search(r"\w+Instrumentor\(\)\.instrument\(|\bTracerProvider\(", py_code)
        or re.search(r"registerInstrumentations\(|new NodeSDK\(|@arizeai/phoenix-otel", js_code)
    )
    project = None
    if found := re.search(r"project_name\s*=\s*[\"']([\w.-]+)[\"']", py_code) or re.search(
        r"projectName\s*:\s*[\"'`]([\w.-]+)[\"'`]", js_code
    ):
        project = found[1]
    deps = (py_deps if language == "python" else js_deps) or ""
    code = py_code if language == "python" else js_code
    framework = next(
        (name for word, name in FRAMEWORKS if word in deps or re.search(rf"\b{word}\b", code)),
        None,
    )
    return {
        "language": language,
        "register": register,
        "otel": otel or register,
        "project": project,
        "framework": framework,
    }


def _python_otlp_snippet(endpoint: str, target: str, framework: str | None) -> str:
    packages = "opentelemetry-sdk opentelemetry-exporter-otlp-proto-http"
    instrument = ["# Turn on your LLM library's OpenTelemetry instrumentation here."]
    if framework in PY_INSTRUMENTORS:
        module, cls = PY_INSTRUMENTORS[framework]
        packages += f" openinference-instrumentation-{framework}"
        instrument = [
            f"from openinference.instrumentation.{module} import {cls}",
            f"{cls}().instrument()",
        ]
    resource = f'Resource.create({{"{TARGET_ATTRIBUTE}": "{target}"}})'
    return "\n".join(
        [
            f"# pip install {packages}",
            "from opentelemetry import trace",
            "from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter",
            "from opentelemetry.sdk.resources import Resource",
            "from opentelemetry.sdk.trace import TracerProvider",
            "from opentelemetry.sdk.trace.export import BatchSpanProcessor",
            "",
            f"provider = TracerProvider(resource={resource})",
            "provider.add_span_processor(",
            f'    BatchSpanProcessor(OTLPSpanExporter(endpoint="{endpoint}/v1/traces"))',
            ")",
            "trace.set_tracer_provider(provider)",
            *instrument,
            "",
            "# Put each conversation's id on its spans, as session.id or gen_ai.conversation.id.",
        ]
    )


def _js_otlp_snippet(endpoint: str, target: str, framework: str | None) -> str:
    name = framework if framework in JS_CLASSES else "openai"
    cls = JS_CLASSES[name]
    package = f"@arizeai/openinference-instrumentation-{name}"
    return "\n".join(
        [
            "// npm install @opentelemetry/sdk-node @opentelemetry/resources \\",
            f"//   @opentelemetry/exporter-trace-otlp-proto {package}",
            'import { NodeSDK } from "@opentelemetry/sdk-node";',
            'import { resourceFromAttributes } from "@opentelemetry/resources";',
            'import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto";',
            f'import {{ {cls} }} from "{package}";',
            "",
            "new NodeSDK({",
            f'  resource: resourceFromAttributes({{ "{TARGET_ATTRIBUTE}": "{target}" }}),',
            f'  traceExporter: new OTLPTraceExporter({{ url: "{endpoint}/v1/traces" }}),',
            f"  instrumentations: [new {cls}()],",
            "}).start();",
            "",
            "// Put each conversation's id on its spans, as session.id or gen_ai.conversation.id.",
        ]
    )


def advice(found: dict[str, Any] | None, url: str, project: str) -> dict[str, Any]:
    """What to tell the person: ``text`` (one or two plain sentences), ``lines`` (to copy),
    ``command`` (to run after, if any) and ``traced`` (the app sends traces at all).

    :param url: Where evalution takes traces, as this computer reaches it.
    """
    target = project.removeprefix(TARGET_PREFIX)
    found = found or {}
    if found.get("register"):
        text = "Set these in your app's environment, then restart it."
        if found.get("project"):
            text += (
                f' Its register() call names project "{found["project"]}"; take that out so '
                "the name below is used."
            )
        lines = f"PHOENIX_COLLECTOR_ENDPOINT={url}\nPHOENIX_PROJECT_NAME={project}"
        return {"traced": True, "text": text, "lines": lines, "command": None}
    if found.get("otel"):
        lines = "\n".join(
            [
                f"OTEL_EXPORTER_OTLP_ENDPOINT={url}",
                "OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf",
                f"OTEL_RESOURCE_ATTRIBUTES={TARGET_ATTRIBUTE}={target}",
                "OTEL_METRICS_EXPORTER=none",
                "OTEL_LOGS_EXPORTER=none",
            ]
        )
        text = "Set these in your app's environment, then restart it."
        if found.get("project"):
            text += f' Its code names project "{found["project"]}"; take that out.'
        return {"traced": True, "text": text, "lines": lines, "command": None}
    make = _js_otlp_snippet if found.get("language") == "js" else _python_otlp_snippet
    snippet = make(url, target, found.get("framework"))
    return {
        "traced": False,
        "text": "Your app doesn't send traces. Tests will still play and be graded on its "
        "replies, but steps and context need tracing. Add this where your app starts:",
        "lines": snippet,
        "command": None,
    }
