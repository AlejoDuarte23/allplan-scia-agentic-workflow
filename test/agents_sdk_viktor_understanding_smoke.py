from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from agents import Agent, Runner, function_tool, set_tracing_disabled

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "test" / "artifacts" / "agents_sdk_viktor_understanding" / "latest.json"

APP_A_URL = "https://demo.viktor.ai/workspaces/2544/app/editor/12039"
APP_B_URL = "https://demo.viktor.ai/workspaces/2515/app/editor/12038"
EDITOR_URL_PATTERN = re.compile(
    r"^https?://(?P<host>[^/]+)/workspaces/(?P<workspace_id>\d+)/app/editor/(?P<entity_id>\d+)(?:[/?#].*)?$"
)
CONTAINER_TYPES = {
    "page",
    "step",
    "tab",
    "section",
    "linebreak",
    "text",
    "setparamsbutton",
}
ACTION_TYPES = {
    "button",
    "downloadbutton",
    "download-button",
    "setparamsbutton",
}


def load_env_file(path: Path = REPO_ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(
            key.strip().removeprefix("export ").strip(),
            value.strip().strip('"').strip("'"),
        )


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing {name}. Add it to .env or export it before running.")
    return value


def parse_editor_url(editor_url: str) -> tuple[str, int, int]:
    match = EDITOR_URL_PATTERN.match(editor_url)
    if not match:
        raise ValueError(f"Unsupported VIKTOR editor URL: {editor_url}")
    return (
        f"https://{match.group('host')}/api",
        int(match.group("workspace_id")),
        int(match.group("entity_id")),
    )


def request_json(
    *,
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"{method} {url} failed ({exc.code}): {detail}") from exc


def get_by_path(payload: dict[str, Any], path: list[str]) -> Any:
    current: Any = payload
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def option_summary(options: Any) -> Any:
    if not options:
        return None
    if isinstance(options, list):
        summarized = []
        for option in options:
            if isinstance(option, dict):
                summarized.append(
                    {
                        "label": option.get("label") or option.get("name") or option.get("value"),
                        "value": option.get("value"),
                        "visible": option.get("visible"),
                    }
                )
            else:
                summarized.append(option)
        return summarized
    return options


def node_path_from_name(name: Any, parent_path: list[str]) -> list[str]:
    if not name:
        return parent_path
    name_text = str(name)
    if "." in name_text:
        return [part for part in name_text.split(".") if part]
    return parent_path + [name_text]


def flatten_parametrization(
    nodes: list[dict[str, Any]],
    *,
    saved_properties: dict[str, Any],
    parent_path: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    parent_path = parent_path or []
    fields: list[dict[str, Any]] = []
    method_refs: list[dict[str, Any]] = []

    for node in nodes:
        raw_type = str(node.get("type") or "")
        name = node.get("name")
        node_path = node_path_from_name(name, parent_path)
        label = node.get("title") or node.get("ui_name") or node.get("label") or name

        for view_method in node.get("views") or []:
            method_refs.append(
                {
                    "method_name": view_method,
                    "source": "parametrization-node-view",
                    "path": ".".join(node_path),
                    "label": label,
                    "kind": "view",
                }
            )

        if node.get("method"):
            method_refs.append(
                {
                    "method_name": node["method"],
                    "source": "parametrization-node-method",
                    "path": ".".join(node_path),
                    "label": label,
                    "kind": raw_type or "method",
                }
            )

        content = node.get("content") or []
        if content:
            child_fields, child_methods = flatten_parametrization(
                content,
                saved_properties=saved_properties,
                parent_path=node_path,
            )
            fields.extend(child_fields)
            method_refs.extend(child_methods)

        normalized_type = raw_type.lower()
        if (
            not name
            or content
            or normalized_type in CONTAINER_TYPES
            or normalized_type in ACTION_TYPES
        ):
            continue

        fields.append(
            {
                "path": ".".join(node_path),
                "label": label,
                "raw_type": raw_type,
                "default": node.get("default"),
                "saved_value": get_by_path(saved_properties, node_path),
                "options": option_summary(node.get("options")),
                "description": node.get("description"),
            }
        )

    return fields, method_refs


def summarize_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"raw_type": type(result).__name__, "value": result}

    summary: dict[str, Any] = {"result_keys": list(result.keys())}
    for key in ("data", "table", "geometry", "plotly", "download", "set_params"):
        value = result.get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            encoded = json.dumps(value, default=str)
            summary[key] = {
                "type": "dict",
                "keys": list(value.keys()),
                "sample": value if len(encoded) < 2500 else None,
            }
        elif isinstance(value, list):
            summary[key] = {"type": "list", "length": len(value), "sample": value[:5]}
        else:
            summary[key] = {"type": type(value).__name__, "value": value}
    return summary


def poll_job(job_url: str, token: str, *, max_seconds: int = 90) -> dict[str, Any]:
    deadline = time.monotonic() + max_seconds
    sleep_s = 0.8
    while time.monotonic() < deadline:
        job = request_json(method="GET", url=job_url, token=token)
        if job.get("status") in {"success", "failed"}:
            return job
        time.sleep(sleep_s)
        sleep_s = min(sleep_s * 1.5, 5.0)
    raise TimeoutError(f"Job did not finish within {max_seconds} seconds.")


def run_method_probe(
    *,
    api_base: str,
    workspace_id: int,
    entity_id: int,
    token: str,
    method_name: str,
    params: dict[str, Any],
    editor_session: str,
) -> dict[str, Any]:
    created = request_json(
        method="POST",
        url=f"{api_base}/workspaces/{workspace_id}/entities/{entity_id}/jobs/",
        token=token,
        payload={
            "method_name": method_name,
            "params": params,
            "editor_session": editor_session,
            "poll_result": False,
            "timeout": 86400,
        },
    )
    if created.get("url"):
        job = poll_job(created["url"], token)
    else:
        job = created
    result = job.get("result") or job.get("content")
    return {
        "method_name": method_name,
        "status": job.get("status"),
        "kind": job.get("kind"),
        "error": job.get("error") or job.get("error_message"),
        "result_summary": summarize_result(result),
    }


def fetch_app_understanding(editor_url: str, token: str) -> dict[str, Any]:
    api_base, workspace_id, entity_id = parse_editor_url(editor_url)
    query = urlencode(
        {
            "properties": "true",
            "clean_params": "true",
            "param_types": "true",
        }
    )
    entity = request_json(
        method="GET",
        url=f"{api_base}/workspaces/{workspace_id}/entities/{entity_id}/?{query}",
        token=token,
    )
    entity_type = request_json(
        method="GET",
        url=f"{api_base}/workspaces/{workspace_id}/entity_types/{entity['entity_type']}/",
        token=token,
    )
    session = request_json(
        method="POST",
        url=f"{api_base}/workspaces/{workspace_id}/entities/{entity_id}/session/",
        token=token,
        payload={},
    )
    editor_session = session["editor_session"]
    saved_properties = entity.get("properties") or {}
    parametrization = request_json(
        method="POST",
        url=f"{api_base}/workspaces/{workspace_id}/entities/{entity_id}/parametrization/",
        token=token,
        payload={
            "editor_session": editor_session,
            "params": saved_properties,
        },
    )

    content = parametrization.get("content") or {}
    fields, method_refs = flatten_parametrization(
        content.get("parametrization") or [],
        saved_properties=saved_properties,
    )
    for index, view in enumerate(content.get("views") or []):
        method_name = view.get("controller_method")
        if method_name:
            method_refs.append(
                {
                    "method_name": method_name,
                    "source": "parametrization-content-view",
                    "path": f"content.views[{index}]",
                    "label": view.get("label"),
                    "kind": view.get("view_type") or "view",
                }
            )

    entity_type_views = []
    for view in entity_type.get("views") or []:
        method_name = view.get("controller_method")
        entity_type_views.append(
            {
                "method_name": method_name,
                "label": view.get("label"),
                "view_type": view.get("view_type"),
                "automatic_update": view.get("automatic_update"),
            }
        )
        if method_name:
            method_refs.append(
                {
                    "method_name": method_name,
                    "source": "entity-type-view",
                    "path": "entity_type.views",
                    "label": view.get("label"),
                    "kind": view.get("view_type") or "view",
                }
            )

    unique_methods = []
    seen_methods: set[tuple[str | None, str, str]] = set()
    for method in method_refs:
        key = (method.get("method_name"), method.get("source") or "", method.get("path") or "")
        if key in seen_methods:
            continue
        seen_methods.add(key)
        unique_methods.append(method)

    return {
        "editor_url": editor_url,
        "workspace_id": workspace_id,
        "entity_id": entity_id,
        "entity_name": entity.get("name"),
        "entity_type": entity.get("entity_type"),
        "entity_type_name": entity.get("entity_type_name"),
        "saved_properties": saved_properties,
        "param_types": entity.get("param_types") or {},
        "fields": fields,
        "entity_type_views": entity_type_views,
        "view_or_action_methods": unique_methods,
        "worker_payload_template": saved_properties,
        "editor_session": editor_session,
    }


@function_tool
def inspect_viktor_apps(app_a_url: str, app_b_url: str) -> str:
    """Read VIKTOR editor apps and return parametrization fields, defaults, payloads, and methods."""
    token = require_env("TOKEN_VK_APP")
    app_a = fetch_app_understanding(app_a_url, token)
    app_b = fetch_app_understanding(app_b_url, token)
    app_a_result_probes = []
    for view in app_a["entity_type_views"]:
        view_type = str(view.get("view_type") or "").lower()
        if view_type not in {"data", "table"} or not view.get("method_name"):
            continue
        try:
            app_a_result_probes.append(
                run_method_probe(
                    api_base=parse_editor_url(app_a_url)[0],
                    workspace_id=app_a["workspace_id"],
                    entity_id=app_a["entity_id"],
                    token=token,
                    method_name=view["method_name"],
                    params=app_a["worker_payload_template"],
                    editor_session=app_a["editor_session"],
                )
            )
        except Exception as exc:
            app_a_result_probes.append(
                {
                    "method_name": view["method_name"],
                    "status": "error",
                    "error": str(exc),
                }
            )
    app_a.pop("editor_session", None)
    app_b.pop("editor_session", None)
    app_a["result_probes"] = app_a_result_probes
    snapshot = {
        "read_only": True,
        "app_a": app_a,
        "app_b": app_b,
    }
    return json.dumps(snapshot)


async def run_agent(*, model: str, output_path: Path) -> str:
    load_env_file()
    require_env("OPENAI_API_KEY")
    require_env("TOKEN_VK_APP")
    set_tracing_disabled(True)

    agent = Agent(
        name="VIKTOR app understanding smoke",
        model=model,
        instructions=(
            "You are testing the VIKTOR app-understanding tool. "
            "Use inspect_viktor_apps exactly once. Treat App A as the turbine data app "
            "and App B as the foundation/SCIA app. Return only one JSON object. "
            "Explain both parametrizations, defaults vs saved values, available result/view methods, "
            "and which App A result or input can feed App B inputs. Prefer App A result_probes "
            "over guesses from method names. "
            "If a mapping is inferred from engineering meaning rather than directly proven by schema, "
            "mark it as inferred and give the reason. Do not claim a mapping exists when App B lacks a matching input."
        ),
        tools=[inspect_viktor_apps],
    )

    prompt = {
        "task": "Understand both VIKTOR app parametrizations and decide what can flow from App A into App B.",
        "app_a_url": APP_A_URL,
        "app_b_url": APP_B_URL,
        "required_output_shape": {
            "run_summary": {},
            "app_a": {},
            "app_b": {},
            "candidate_flow_app_a_to_app_b": [],
            "gaps_before_bridge_code": [],
        },
    }
    result = await Runner.run(agent, json.dumps(prompt), max_turns=8)
    final_text = str(result.final_output)
    try:
        agent_output: Any = json.loads(final_text)
    except json.JSONDecodeError:
        agent_output = final_text

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            {
                "run_at": datetime.now(timezone.utc).isoformat(),
                "model": model,
                "app_a_url": APP_A_URL,
                "app_b_url": APP_B_URL,
                "agent_output": agent_output,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return final_text


def main() -> None:
    parser = argparse.ArgumentParser(description="Agents SDK smoke for VIKTOR app understanding.")
    parser.add_argument("--model", default=os.getenv("OPENAI_AGENT_MODEL", "gpt-5.4"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    final_text = asyncio.run(run_agent(model=args.model, output_path=args.output))
    print(final_text)
    print(f"\nSaved artifact: {args.output}")


if __name__ == "__main__":
    main()
