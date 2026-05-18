from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import types
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "test" / "artifacts" / "agent_runner_real_two_viktor_urls" / "latest.json"
sys.path.insert(0, str(REPO_ROOT))

URLS = [
    "https://demo.viktor.ai/workspaces/2544/app/editor/12039",
    "https://demo.viktor.ai/workspaces/2515/app/editor/12040",
]


class _StubFile:
    def __init__(self, data: str | bytes) -> None:
        self._data = data.encode("utf-8") if isinstance(data, str) else data

    @classmethod
    def from_data(cls, data: str | bytes) -> "_StubFile":
        return cls(data)

    def getvalue_binary(self) -> bytes:
        return self._data

    def getvalue(self) -> str:
        return self._data.decode("utf-8")


class _StubStorage:
    _values: dict[str, _StubFile] = {}

    def set(self, key: str, *, data: _StubFile, scope: str = "entity") -> None:
        self._values[f"{scope}:{key}"] = data

    def get(self, key: str, *, scope: str = "entity") -> _StubFile:
        return self._values[f"{scope}:{key}"]

    def delete(self, key: str, *, scope: str = "entity") -> None:
        self._values.pop(f"{scope}:{key}", None)

    @classmethod
    def clear(cls) -> None:
        cls._values.clear()


def _decorator(*args, **kwargs):
    def inner(fn):
        return fn

    return inner


def install_viktor_stub() -> None:
    module = types.ModuleType("viktor")
    module.File = _StubFile
    module.Storage = _StubStorage
    module.Controller = type("Controller", (), {})
    module.Parametrization = type("Parametrization", (), {})
    module.Chat = type("Chat", (), {"__init__": lambda self, *args, **kwargs: None})
    module.ChatResult = type("ChatResult", (), {"__init__": lambda self, *args, **kwargs: None})
    module.WebResult = type("WebResult", (), {"__init__": lambda self, *args, **kwargs: None})
    module.TableResult = type("TableResult", (), {"__init__": lambda self, *args, **kwargs: None})
    module.Text = type("Text", (), {"__init__": lambda self, *args, **kwargs: None})
    module.WebView = _decorator
    module.TableView = _decorator
    sys.modules["viktor"] = module


def _storage_text(key: str, *, scope: str = "entity") -> str:
    return _StubStorage._values[f"{scope}:{key}"].getvalue()


def _storage_json(key: str, *, scope: str = "entity") -> object:
    return json.loads(_storage_text(key, scope=scope))


async def run_smoke(*, model: str, output: Path) -> dict[str, object]:
    load_dotenv(REPO_ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("Missing OPENAI_API_KEY in environment or .env.")
    if not (os.getenv("TOKEN_VK_APP") or os.getenv("VIKTOR_TOKEN")):
        raise RuntimeError("Missing TOKEN_VK_APP or VIKTOR_TOKEN in environment or .env.")

    install_viktor_stub()

    from agents import Runner

    from app.agent.runner import AgentContext, create_workflow_agent
    from app.viktor_api_tool.code_editor import CODE_STORAGE_KEY, CODE_VISIBILITY_KEY
    from app.workflow_graph.state import WORKFLOW_GRAPH_STATE_STORAGE_KEY, load_canvas_state

    _StubStorage.clear()
    agent = create_workflow_agent(model=model)
    prompt = f"""
Real VIKTOR two-link smoke. Use these URLs:
1. {URLS[0]}
2. {URLS[1]}

Do real VIKTOR API work:
- Inspect URL 1 with inspect_viktor_app and probe_output_methods=true.
- Inspect URL 2 with inspect_viktor_app and probe_output_methods=true.
- If SCIA/foundation output methods fail because a file, worker, integration,
  credential, or .esa template is missing, mark that requirement as blocked.
  Do not fail the workflow.

After inspection:
- Tell the working interpretation: URL 1 is the source/load candidate and URL 2
  is the foundation/SCIA downstream candidate unless inspected data contradicts this.
- Create a workflow graph named "Real Two URL Smoke" with:
  loads_app -> foundation_scia_app.
- Save a code-view artifact named "real_two_url_findings.md" summarizing:
  inspected inputs/defaults, DataView/TableView methods, method probe status,
  candidate load-to-foundation mappings, downstream inputs kept as default/saved,
  and any blocked SCIA requirements.
- Save a second code-view artifact named "real_two_url_bridge.py" containing a
  runnable Python bridge skeleton. It must:
  - read TOKEN_VK_APP or VIKTOR_TOKEN from the environment
  - call URL 1 DataView method `view_turbine_data`
  - build URL 2 payload from URL 2 saved/default values
  - map candidate load fields into:
    `step_geo.sec_mast.mast_diameter`,
    `step_geo.sec_mast.mast_vertical_load`,
    `step_geo.sec_mast.mast_horizontal_load`,
    `step_geo.sec_mast.mast_moment`
  - preserve downstream geometry/geotech defaults or saved values
  - detect missing `step_analysis.esa_file` and log it as BLOCKED instead of failing
  - include TODO comments where the exact URL 1 DataView schema is not proven

Call save_workflow_code with files={{
  "real_two_url_findings.md": "<your markdown content>",
  "real_two_url_bridge.py": "<your Python code>"
}}.
Show the code editor and return to the user with the partial workflow and blocked requirements.
"""
    result = await Runner.run(
        agent,
        [{"role": "user", "content": prompt}],
        context=AgentContext(),
        max_turns=24,
    )

    state = load_canvas_state()
    if state is None:
        raise RuntimeError("Agent did not create workflow graph state.")

    stored_keys = sorted(_StubStorage._values)
    capture_keys = [
        key
        for key in stored_keys
        if key.startswith("entity:viktor_app_capture_")
    ]
    code_files = _storage_json(CODE_STORAGE_KEY)
    code_visibility = _storage_text(CODE_VISIBILITY_KEY)
    graph_state_stored = f"entity:{WORKFLOW_GRAPH_STATE_STORAGE_KEY}" in _StubStorage._values
    markdown = str(code_files.get("real_two_url_findings.md", ""))
    bridge_code = str(code_files.get("real_two_url_bridge.py", ""))

    artifact: dict[str, object] = {
        "model": model,
        "final_output": str(result.final_output),
        "workflow_name": state.workflow_name,
        "node_ids": [node.id for node in state.workflow.nodes],
        "depends_on": {
            node.id: [dep.node_id for dep in node.depends_on]
            for node in state.workflow.nodes
        },
        "node_urls": {node.id: node.url for node in state.workflow.nodes},
        "plan_ids": [todo.id for todo in state.plan.todos] if state.plan else [],
        "capture_keys": capture_keys,
        "stored_keys": stored_keys,
        "workflow_graph_state_stored": graph_state_stored,
        "code_files": code_files,
        "code_visibility": code_visibility,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")

    required_capture_fragments = [
        "viktor_app_capture_2544_12039",
        "viktor_app_capture_2515_12040",
    ]
    missing_captures = [
        fragment
        for fragment in required_capture_fragments
        if not any(fragment in key for key in capture_keys)
    ]
    if missing_captures:
        raise RuntimeError(f"Missing expected real capture(s): {missing_captures}; got {capture_keys}")
    if "real_two_url_findings.md" not in code_files:
        raise RuntimeError(f"Code artifact was not saved: {sorted(code_files)}")
    if "real_two_url_bridge.py" not in code_files:
        raise RuntimeError(f"Bridge code artifact was not saved: {sorted(code_files)}")
    if code_visibility != "show":
        raise RuntimeError(f"Code editor visibility was not set to show: {code_visibility}")
    if not any(term in markdown.lower() for term in ("blocked", ".esa", "template", "worker", "integration")):
        raise RuntimeError("Findings artifact did not mention blocked SCIA/integration requirements.")
    if not all(term in bridge_code for term in ("TOKEN_VK_APP", "view_turbine_data", "step_analysis", "BLOCKED")):
        raise RuntimeError("Bridge code artifact is missing required bridge/safety markers.")
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the agent with two real VIKTOR URLs.")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    artifact = asyncio.run(run_smoke(model=args.model, output=args.output))
    print(json.dumps(artifact, indent=2))
    print(f"\nSaved artifact: {args.output}")


if __name__ == "__main__":
    main()
