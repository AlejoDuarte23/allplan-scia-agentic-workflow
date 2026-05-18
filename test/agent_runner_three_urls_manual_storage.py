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
DEFAULT_OUTPUT = REPO_ROOT / "test" / "artifacts" / "agent_runner_three_urls_manual_storage" / "latest.json"
sys.path.insert(0, str(REPO_ROOT))

URLS = [
    "https://demo.viktor.ai/workspaces/2544/app/editor/12039",
    "https://demo.viktor.ai/workspaces/2515/app/editor/12040",
    "https://demo.viktor.ai/workspaces/2541/app/",
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
This is a prompt and manual-storage smoke test. Use these three VIKTOR URLs:
1. {URLS[0]}
2. {URLS[1]}
3. {URLS[2]}

Do not inspect VIKTOR apps. Do not run shell. Use the URL order only.
Create a workflow graph named "Three URL Smoke" with:
- loads_app: type viktor_api_tool, label "Load source app", URL 1
- foundation_app: type scia_model, label "Foundation/SCIA downstream app", URL 2, depends on loads_app
- rebar_app: type viktor_api_tool, label "Rebar downstream app", URL 3, depends on foundation_app

Then save a code-view artifact named "three_url_workflow.md" that says:
- URL 1 is assumed to be the source/load candidate until inspection confirms it.
- URL 2 and URL 3 are downstream candidates.
- Downstream defaults and saved values must be preserved for inputs not clearly supplied by upstream outputs.
- Mappings are candidate until actual DataView/TableView outputs are inspected.

Call save_workflow_code with files={{"three_url_workflow.md": "<your markdown content>"}}.
Show the code editor and answer briefly.
"""
    result = await Runner.run(
        agent,
        [{"role": "user", "content": prompt}],
        context=AgentContext(),
        max_turns=10,
    )

    state = load_canvas_state()
    if state is None:
        raise RuntimeError("Agent did not create workflow graph state.")

    node_ids = [node.id for node in state.workflow.nodes]
    depends_on = {
        node.id: [dep.node_id for dep in node.depends_on]
        for node in state.workflow.nodes
    }
    urls_by_node = {node.id: node.url for node in state.workflow.nodes}
    code_files = json.loads(_storage_text(CODE_STORAGE_KEY))
    code_visibility = _storage_text(CODE_VISIBILITY_KEY)

    expected_nodes = ["loads_app", "foundation_app", "rebar_app"]
    if node_ids != expected_nodes:
        raise RuntimeError(f"Unexpected nodes: {node_ids}")
    if depends_on != {
        "loads_app": [],
        "foundation_app": ["loads_app"],
        "rebar_app": ["foundation_app"],
    }:
        raise RuntimeError(f"Unexpected dependencies: {depends_on}")
    if urls_by_node != dict(zip(expected_nodes, URLS, strict=True)):
        raise RuntimeError(f"Unexpected node URLs: {urls_by_node}")
    if "three_url_workflow.md" not in code_files:
        raise RuntimeError(f"Code artifact was not saved: {sorted(code_files)}")
    if code_visibility != "show":
        raise RuntimeError(f"Code editor visibility was not set to show: {code_visibility}")

    artifact: dict[str, object] = {
        "model": model,
        "final_output": str(result.final_output),
        "workflow_name": state.workflow_name,
        "node_ids": node_ids,
        "depends_on": depends_on,
        "urls_by_node": urls_by_node,
        "plan_ids": [todo.id for todo in state.plan.todos] if state.plan else [],
        "stored_keys": sorted(_StubStorage._values),
        "workflow_graph_state_stored": f"entity:{WORKFLOW_GRAPH_STATE_STORAGE_KEY}" in _StubStorage._values,
        "code_files": code_files,
        "code_visibility": code_visibility,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the agent with three VIKTOR URLs and manual storage.")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    artifact = asyncio.run(run_smoke(model=args.model, output=args.output))
    print(json.dumps(artifact, indent=2))
    print(f"\nSaved artifact: {args.output}")


if __name__ == "__main__":
    main()
