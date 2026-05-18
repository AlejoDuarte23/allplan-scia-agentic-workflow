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
DEFAULT_OUTPUT = REPO_ROOT / "test" / "artifacts" / "agent_runner_create_workflow" / "latest.json"
sys.path.insert(0, str(REPO_ROOT))


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
    if "viktor" in sys.modules:
        return

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


async def run_smoke(*, model: str, output: Path) -> dict[str, object]:
    load_dotenv(REPO_ROOT / ".env")
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("Missing OPENAI_API_KEY in environment or .env.")
    if not (os.getenv("TOKEN_VK_APP") or os.getenv("VIKTOR_TOKEN")):
        raise RuntimeError("Missing TOKEN_VK_APP or VIKTOR_TOKEN in environment or .env.")

    install_viktor_stub()

    from agents import Runner

    from app.agent.runner import AgentContext, create_workflow_agent
    from app.workflow_graph.state import load_canvas_state

    _StubStorage.clear()
    agent = create_workflow_agent(model=model)
    prompt = """
Create a workflow graph named "Agent Runner Smoke" with exactly these nodes:
- source: workflow_step, label "Source Data"
- scia: scia_model, label "SCIA Analysis", depends on source
- report: table_output, label "Result Table", depends on scia

Do not inspect VIKTOR apps. Do not use shell. Create the graph first, then answer briefly.
"""
    result = await Runner.run(
        agent,
        [{"role": "user", "content": prompt}],
        context=AgentContext(),
        max_turns=8,
    )

    state = load_canvas_state()
    if state is None:
        raise RuntimeError("Agent did not create workflow graph state.")

    node_ids = [node.id for node in state.workflow.nodes]
    depends_on = {
        node.id: [dep.node_id for dep in node.depends_on]
        for node in state.workflow.nodes
    }
    plan_ids = [todo.id for todo in state.plan.todos] if state.plan else []

    expected_nodes = ["source", "scia", "report"]
    if node_ids != expected_nodes:
        raise RuntimeError(f"Unexpected nodes: {node_ids}")
    if depends_on != {"source": [], "scia": ["source"], "report": ["scia"]}:
        raise RuntimeError(f"Unexpected dependencies: {depends_on}")
    if plan_ids != expected_nodes:
        raise RuntimeError(f"Unexpected plan todos: {plan_ids}")

    artifact: dict[str, object] = {
        "model": model,
        "final_output": str(result.final_output),
        "workflow_name": state.workflow_name,
        "node_ids": node_ids,
        "depends_on": depends_on,
        "plan_ids": plan_ids,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    return artifact


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the app.agent runner workflow creation path.")
    parser.add_argument("--model", default="gpt-5.4")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    artifact = asyncio.run(run_smoke(model=args.model, output=args.output))
    print(json.dumps(artifact, indent=2))
    print(f"\nSaved artifact: {args.output}")


if __name__ == "__main__":
    main()
