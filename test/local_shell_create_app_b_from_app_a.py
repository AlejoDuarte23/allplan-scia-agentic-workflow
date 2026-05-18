from __future__ import annotations

import argparse
import asyncio
import base64
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "test" / "artifacts" / "local_shell_create_app_b_from_app_a" / "latest.json"
APP_A_URL = "https://demo.viktor.ai/workspaces/2544/app/editor/12039"
APP_B_SOURCE_URL = "https://demo.viktor.ai/workspaces/2515/app/editor/12038"


def load_local_shell_module() -> Any:
    module_path = REPO_ROOT / "app" / "viktor_api_tool" / "local_shell.py"
    spec = importlib.util.spec_from_file_location("viktor_local_shell", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def build_bridge_script(update_entity_id: int | None) -> str:
    update_value = "None" if update_entity_id is None else str(update_entity_id)
    return f'''
import json, os, re, time
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

APP_A_URL = {APP_A_URL!r}
APP_B_SOURCE_URL = {APP_B_SOURCE_URL!r}
UPDATE_ENTITY_ID = {update_value}
TOKEN = os.environ["TOKEN_VK_APP"]
EDITOR_URL_PATTERN = re.compile(r"^https?://(?P<host>[^/]+)/workspaces/(?P<workspace_id>\\d+)/app/editor/(?P<entity_id>\\d+)(?:[/?#].*)?$")
CONTAINER_TYPES = {{"page", "step", "tab", "section", "linebreak", "text", "setparamsbutton"}}
ACTION_TYPES = {{"button", "downloadbutton", "download-button", "setparamsbutton"}}

def parse_editor_url(editor_url):
    match = EDITOR_URL_PATTERN.match(editor_url)
    if not match:
        raise ValueError("Unsupported editor URL: " + editor_url)
    return f"https://{{match.group('host')}}/api", int(match.group("workspace_id")), int(match.group("entity_id"))

def request_json(method, url, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = Request(url, data=body, method=method, headers={{"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}})
    try:
        with urlopen(req, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"{{method}} {{url}} failed ({{exc.code}}): {{detail}}") from exc

def editor_url(api_base, workspace_id, entity_id):
    return f"{{api_base.rstrip('/').removesuffix('/api')}}/workspaces/{{workspace_id}}/app/editor/{{entity_id}}"

def node_path_from_name(name, parent_path):
    if not name:
        return parent_path
    name = str(name)
    return [part for part in name.split(".") if part] if "." in name else parent_path + [name]

def set_by_path(payload, path, value):
    current = payload
    for part in path[:-1]:
        current = current.setdefault(part, {{}})
    current[path[-1]] = value

def deep_merge(base, extra):
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            deep_merge(base[key], value)
        else:
            base[key] = value
    return base

def collect_defaults(nodes, parent_path=None):
    parent_path = parent_path or []
    payload = {{}}
    for node in nodes:
        raw_type = str(node.get("type") or "")
        normalized = raw_type.lower()
        name = node.get("name")
        node_path = node_path_from_name(name, parent_path)
        content = node.get("content") or []
        if content:
            deep_merge(payload, collect_defaults(content, node_path))
        if not name or content or normalized in CONTAINER_TYPES or normalized in ACTION_TYPES:
            continue
        set_by_path(payload, node_path, node.get("default"))
    return payload

def flatten_data_items(items):
    result = {{}}
    def visit(item, group=None):
        children = item.get("children") or []
        next_group = item.get("key") or item.get("label") or group if children else group
        if not children and item.get("key"):
            result[str(item["key"])] = {{
                "group": group,
                "label": item.get("label"),
                "value": item.get("value"),
                "unit": item.get("suffix") or "",
                "display_value": item.get("display_value"),
            }}
        for child in children:
            visit(child, next_group)
    for item in items:
        visit(item)
    return result

def poll_job(job_url):
    deadline = time.monotonic() + 90
    delay = 0.8
    while time.monotonic() < deadline:
        job = request_json("GET", job_url)
        if job.get("status") in {{"success", "failed"}}:
            return job
        time.sleep(delay)
        delay = min(delay * 1.5, 5)
    raise TimeoutError("VIKTOR job did not finish")

def run_method(api_base, workspace_id, entity_id, method_name, params, editor_session):
    created = request_json("POST", f"{{api_base}}/workspaces/{{workspace_id}}/entities/{{entity_id}}/jobs/", {{
        "method_name": method_name,
        "params": params,
        "editor_session": editor_session,
        "poll_result": False,
        "timeout": 86400,
    }})
    return poll_job(created["url"]) if created.get("url") else created

def get_parent_id(api_base, workspace_id, entity_id):
    try:
        parent = request_json("GET", f"{{api_base}}/workspaces/{{workspace_id}}/entities/{{entity_id}}/parent/")
        return int(parent["id"])
    except Exception:
        return None

app_a_api, app_a_ws, app_a_id = parse_editor_url(APP_A_URL)
app_b_api, app_b_ws, app_b_id = parse_editor_url(APP_B_SOURCE_URL)
app_a = request_json("GET", f"{{app_a_api}}/workspaces/{{app_a_ws}}/entities/{{app_a_id}}/?{{urlencode({{'properties':'true','clean_params':'true'}})}}")
app_a_session = request_json("POST", f"{{app_a_api}}/workspaces/{{app_a_ws}}/entities/{{app_a_id}}/session/", {{}})
job = run_method(app_a_api, app_a_ws, app_a_id, "view_turbine_data", app_a.get("properties") or {{}}, app_a_session["editor_session"])
if job.get("status") != "success":
    raise RuntimeError("App A view_turbine_data failed: " + json.dumps(job)[:500])
outputs = flatten_data_items((job.get("result") or {{}}).get("data") or [])
app_b = request_json("GET", f"{{app_b_api}}/workspaces/{{app_b_ws}}/entities/{{app_b_id}}/?{{urlencode({{'properties':'true','clean_params':'true','param_types':'true'}})}}")
app_b_session = request_json("POST", f"{{app_b_api}}/workspaces/{{app_b_ws}}/entities/{{app_b_id}}/session/", {{}})
parametrization = request_json("POST", f"{{app_b_api}}/workspaces/{{app_b_ws}}/entities/{{app_b_id}}/parametrization/", {{
    "editor_session": app_b_session["editor_session"],
    "params": {{}},
}})
payload = collect_defaults((parametrization.get("content") or {{}}).get("parametrization") or [])
mappings = {{
    "base_diameter": ["step_geo", "sec_mast", "mast_diameter"],
    "base_vert_force": ["step_geo", "sec_mast", "mast_vertical_load"],
    "base_horiz_force": ["step_geo", "sec_mast", "mast_horizontal_load"],
    "base_moment": ["step_geo", "sec_mast", "mast_moment"],
}}
applied = []
for source_key, target_path in mappings.items():
    value = outputs[source_key]["value"]
    set_by_path(payload, target_path, value)
    applied.append({{"from": source_key, "to": ".".join(target_path), "value": value, "unit": outputs[source_key]["unit"]}})

if UPDATE_ENTITY_ID is not None:
    existing = request_json("GET", f"{{app_b_api}}/workspaces/{{app_b_ws}}/entities/{{UPDATE_ENTITY_ID}}/?{{urlencode({{'properties':'true','clean_params':'true'}})}}")
    created = request_json("PUT", f"{{app_b_api}}/workspaces/{{app_b_ws}}/entities/{{UPDATE_ENTITY_ID}}/", {{
        "name": existing["name"],
        "properties": payload,
        "message": "Update App B from App A through local shell executor",
    }})
else:
    parent_id = get_parent_id(app_b_api, app_b_ws, app_b_id)
    create_url = f"{{app_b_api}}/workspaces/{{app_b_ws}}/entities/{{parent_id}}/entities/" if parent_id is not None else f"{{app_b_api}}/workspaces/{{app_b_ws}}/entities/"
    created = request_json("POST", create_url, {{
        "entity_type": app_b["entity_type"],
        "name": "Local shell bridge from " + app_a["name"] + " " + datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "properties": payload,
    }})
    if isinstance(created, list):
        created = created[0]

created_id = int(created["id"])
print(json.dumps({{
    "created_entity_id": created_id,
    "created_entity_name": created.get("name"),
    "created_editor_url": editor_url(app_b_api, app_b_ws, created_id),
    "applied_mappings": applied,
    "created_properties": payload,
}}, indent=2))
'''


async def run_smoke(*, output: Path, update_entity_id: int | None) -> None:
    load_env_file()
    token = require_env("TOKEN_VK_APP")
    local_shell = load_local_shell_module()
    script = build_bridge_script(update_entity_id)
    encoded = base64.b64encode(script.encode("utf-8")).decode("ascii")
    command = f"python -c \"import base64; exec(base64.b64decode('{encoded}'))\""
    result = await local_shell.run_local_viktor_shell_commands(
        commands=[command],
        token=token,
        app_urls=[APP_A_URL, APP_B_SOURCE_URL],
    )
    shell_stdout = ""
    for command_log in result.get("commands", []):
        shell_stdout += command_log.get("stdout") or ""
    parsed_stdout = None
    try:
        parsed_stdout = json.loads(shell_stdout)
    except json.JSONDecodeError:
        pass
    artifact = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "app_a_url": APP_A_URL,
        "app_b_source_url": APP_B_SOURCE_URL,
        "result": result,
        "parsed_shell_stdout": parsed_stdout,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps(artifact, indent=2))
    print(f"\nSaved artifact: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create/update App B from App A through the local shell tool.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--update-entity-id", type=int, default=None)
    args = parser.parse_args()

    asyncio.run(
        run_smoke(
            output=args.output,
            update_entity_id=args.update_entity_id,
        )
    )


if __name__ == "__main__":
    main()
