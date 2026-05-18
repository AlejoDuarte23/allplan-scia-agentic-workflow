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
DEFAULT_OUTPUT = REPO_ROOT / "test" / "artifacts" / "local_shell_second_third_test" / "latest.json"

SECOND_APP_URL = "https://demo.viktor.ai/workspaces/2544/app/editor/12039"
THIRD_APP_URL = "https://demo.viktor.ai/workspaces/2515/app/editor/12040"


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


def build_test_script() -> str:
    return f'''
import json, os, re, time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

SECOND_APP_URL = {SECOND_APP_URL!r}
THIRD_APP_URL = {THIRD_APP_URL!r}
TOKEN = os.environ["TOKEN_VK_APP"]
EDITOR_URL_RE = re.compile(r"^https?://(?P<host>[^/]+)/workspaces/(?P<workspace_id>\\d+)/app/editor/(?P<entity_id>\\d+)(?:[/?#].*)?$")

def parse_editor_url(url):
    match = EDITOR_URL_RE.match(url)
    if not match:
        raise ValueError("Unsupported editor URL: " + url)
    return f"https://{{match.group('host')}}/api", int(match.group("workspace_id")), int(match.group("entity_id"))

def request_json(method, url, payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(url, data=body, method=method, headers={{"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"}})
    try:
        with urlopen(request, timeout=120) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"{{method}} {{url}} failed ({{exc.code}}): {{detail}}") from exc

def poll_job(url):
    deadline = time.monotonic() + 90
    delay = 0.8
    while time.monotonic() < deadline:
        job = request_json("GET", url)
        if job.get("status") in {{"success", "failed"}}:
            return job
        time.sleep(delay)
        delay = min(delay * 1.5, 5)
    raise TimeoutError("VIKTOR job did not finish")

def run_method(api, workspace_id, entity_id, method_name, params):
    session = request_json("POST", f"{{api}}/workspaces/{{workspace_id}}/entities/{{entity_id}}/session/", {{}})
    created = request_json("POST", f"{{api}}/workspaces/{{workspace_id}}/entities/{{entity_id}}/jobs/", {{
        "method_name": method_name,
        "params": params,
        "editor_session": session["editor_session"],
        "poll_result": False,
        "timeout": 86400,
    }})
    return poll_job(created["url"]) if created.get("url") else created

def flatten_data(items):
    result = {{}}
    def visit(item, group=None):
        children = item.get("children") or []
        next_group = item.get("key") or item.get("label") or group if children else group
        if not children and item.get("key"):
            result[str(item["key"])] = {{
                "value": item.get("value"),
                "unit": item.get("suffix") or "",
                "label": item.get("label"),
                "group": group,
            }}
        for child in children:
            visit(child, next_group)
    for item in items:
        visit(item)
    return result

def get_path(payload, path):
    current = payload
    for part in path:
        current = current[part]
    return current

second_api, second_ws, second_id = parse_editor_url(SECOND_APP_URL)
third_api, third_ws, third_id = parse_editor_url(THIRD_APP_URL)
second = request_json("GET", f"{{second_api}}/workspaces/{{second_ws}}/entities/{{second_id}}/?{{urlencode({{'properties':'true','clean_params':'true'}})}}")
third = request_json("GET", f"{{third_api}}/workspaces/{{third_ws}}/entities/{{third_id}}/?{{urlencode({{'properties':'true','clean_params':'true'}})}}")
second_job = run_method(second_api, second_ws, second_id, "view_turbine_data", second.get("properties") or {{}})
if second_job.get("status") != "success":
    raise RuntimeError("Second app view_turbine_data failed: " + json.dumps(second_job)[:500])
outputs = flatten_data((second_job.get("result") or {{}}).get("data") or [])
mappings = [
    ("base_diameter", ["step_geo", "sec_mast", "mast_diameter"]),
    ("base_vert_force", ["step_geo", "sec_mast", "mast_vertical_load"]),
    ("base_horiz_force", ["step_geo", "sec_mast", "mast_horizontal_load"]),
    ("base_moment", ["step_geo", "sec_mast", "mast_moment"]),
]
checks = []
for source_key, target_path in mappings:
    source = outputs[source_key]
    target_value = get_path(third.get("properties") or {{}}, target_path)
    checks.append({{
        "from": source_key,
        "to": ".".join(target_path),
        "source_value": source["value"],
        "source_unit": source["unit"],
        "third_app_value": target_value,
        "match": source["value"] == target_value,
    }})
print(json.dumps({{
    "second_app": {{
        "url": SECOND_APP_URL,
        "entity_id": second_id,
        "name": second.get("name"),
        "turbine_model": (second.get("properties") or {{}}).get("turbine_model"),
    }},
    "third_app": {{
        "url": THIRD_APP_URL,
        "entity_id": third_id,
        "name": third.get("name"),
    }},
    "second_app_outputs": outputs,
    "mapping_checks": checks,
    "all_mapped_values_match": all(item["match"] for item in checks),
}}, indent=2))
'''


async def run_test(*, output: Path) -> None:
    load_env_file()
    token = require_env("TOKEN_VK_APP")
    local_shell = load_local_shell_module()
    encoded = base64.b64encode(build_test_script().encode("utf-8")).decode("ascii")
    command = f"python -c \"import base64; exec(base64.b64decode('{encoded}'))\""
    result = await local_shell.run_local_viktor_shell_commands(
        commands=[command],
        token=token,
        app_urls=[SECOND_APP_URL, THIRD_APP_URL],
    )
    stdout = "".join(command.get("stdout") or "" for command in result.get("commands", []))
    parsed_stdout = None
    try:
        parsed_stdout = json.loads(stdout)
    except json.JSONDecodeError:
        pass
    artifact = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "second_app_url": SECOND_APP_URL,
        "third_app_url": THIRD_APP_URL,
        "result": result,
        "parsed_shell_stdout": parsed_stdout,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps(artifact, indent=2))
    print(f"\\nSaved artifact: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only local-shell test for second and third VIKTOR apps.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    asyncio.run(run_test(output=args.output))


if __name__ == "__main__":
    main()
