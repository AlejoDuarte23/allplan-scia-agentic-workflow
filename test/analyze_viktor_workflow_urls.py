from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "test" / "artifacts" / "viktor_five_app_workflow"
DEFAULT_URLS = [
    "https://demo.viktor.ai/workspaces/2564/app/editor/12049",
    "https://demo.viktor.ai/workspaces/2563/app/editor/12048",
    "https://demo.viktor.ai/workspaces/2544/app/editor/12042",
    "https://demo.viktor.ai/workspaces/2541/app/editor/12027",
    "https://demo.viktor.ai/workspaces/2515/app/editor/12040",
]

sys.path.insert(0, str(REPO_ROOT))

from app.viktor_api_tool.client import ViktorApiClient, parse_app_url  # noqa: E402
from app.viktor_api_tool.discovery import capture_app, detect_result_kind  # noqa: E402


def ensure_token() -> None:
    if os.getenv("TOKEN_VK_APP") or os.getenv("VIKTOR_TOKEN"):
        return
    load_dotenv(dotenv_path=REPO_ROOT / ".env")
    if not (os.getenv("TOKEN_VK_APP") or os.getenv("VIKTOR_TOKEN")):
        raise RuntimeError("Missing TOKEN_VK_APP or VIKTOR_TOKEN.")


def json_block(value: Any) -> str:
    return "```json\n" + json.dumps(value, indent=2, ensure_ascii=False) + "\n```"


def flatten_data_items(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    flattened: dict[str, dict[str, Any]] = {}

    def visit(item: dict[str, Any], group: str | None = None) -> None:
        children = item.get("children") or []
        next_group = item.get("key") or item.get("label") or group if children else group
        if not children and item.get("key"):
            flattened[str(item["key"])] = {
                "group": group,
                "label": item.get("label"),
                "value": item.get("value"),
                "display_value": item.get("display_value"),
                "unit": item.get("suffix") or "",
                "status": item.get("status"),
                "status_message": item.get("status_message"),
                "explanation": item.get("explanation_label"),
            }
        for child in children:
            visit(child, next_group)

    for item in items:
        visit(item)
    return flattened


def collect_fields(nodes: list[Any]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []

    def walk(node_list: list[Any]) -> None:
        for node in node_list:
            path = ".".join(node.path)
            if node.node_kind in {"field", "table", "dynamic_array"} and path:
                fields.append(
                    {
                        "path": path,
                        "label": node.label or node.title,
                        "type": node.raw_type,
                        "default": node.default,
                        "metadata": node.metadata,
                    }
                )
            walk(node.children)

    walk(nodes)
    return fields


def role_for_app(capture: Any, fields: list[dict[str, Any]]) -> str:
    field_paths = {field["path"] for field in fields}
    methods = {method.method_name for method in capture.methods}
    if "download_cpt_xml" in methods or "location" in field_paths:
        return "cpt_locator"
    if "tab_input.sec_file.cpt_file" in field_paths:
        return "pile_capacity_cpt"
    if "view_turbine_data" in methods:
        return "turbine_load_source"
    if "tab_loading.combinations" in field_paths:
        return "reinforced_concrete_section"
    if "step_geo.sec_mast.mast_vertical_load" in field_paths:
        return "foundation_scia"
    return "viktor_app"


def methods_to_probe(capture: Any) -> list[str]:
    selected: list[str] = []
    for method in capture.methods:
        if method.expected_result_kind in {"data", "table", "geojson_and_data"}:
            selected.append(method.method_name)
        elif method.method_name in {"map_view", "download_cpt_xml"}:
            selected.append(method.method_name)
    return list(dict.fromkeys(selected))


def summarize_job_result(job: Any) -> dict[str, Any]:
    kind, keys, summary = detect_result_kind(job.result)
    if kind == "download":
        summary = {
            "url_present": bool(summary.get("url")),
            "url_redacted": "[signed download url omitted]",
        }
    result_payload = job.result.model_dump(mode="json", exclude_none=True) if job.result else {}
    details: dict[str, Any] = {
        "status": job.status,
        "result_kind": kind,
        "result_keys": keys,
        "summary": summary,
    }
    if "data" in result_payload:
        details["flattened_data"] = flatten_data_items(result_payload["data"])
    if "geojson" in result_payload:
        geojson = result_payload["geojson"].get("geojson", {})
        details["geojson_feature_count"] = len(geojson.get("features") or [])
    if "table" in result_payload:
        table = result_payload["table"]
        details["table_summary"] = {
            "keys": sorted(table.keys()) if isinstance(table, dict) else [],
            "preview": table if len(json.dumps(table, default=str)) < 1200 else "large table omitted",
        }
    if "download" in result_payload:
        url = (result_payload.get("download") or {}).get("url")
        details["download"] = {
            "url_present": bool(url),
            "host": re.sub(r"^https?://([^/]+).*", r"\1", url or ""),
            "url_redacted": "[signed download url omitted]" if url else None,
        }
    return details


def inspect_apps(urls: list[str]) -> list[dict[str, Any]]:
    apps: list[dict[str, Any]] = []
    for url in urls:
        target = parse_app_url(url)
        client = ViktorApiClient(api_base=target.api_base, max_poll_seconds=75)
        capture = capture_app(client=client, target=target)
        fields = collect_fields(capture.parametrization_tree)
        probes: dict[str, Any] = {}
        params = capture.default_plus_saved_payload.params or capture.entity.properties
        for method_name in methods_to_probe(capture):
            try:
                job = client.create_job(
                    target,
                    method_name=method_name,
                    params=params,
                    timeout=86400,
                )
                probes[method_name] = summarize_job_result(job)
            except Exception as exc:
                probes[method_name] = {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:1500],
                }
        apps.append(
            {
                "url": url,
                "api_base": target.api_base,
                "workspace_id": target.workspace_id,
                "entity_id": target.entity_id,
                "entity": {
                    "name": capture.entity.name,
                    "entity_type": capture.entity.entity_type,
                    "entity_type_name": capture.entity.entity_type_name,
                    "properties": capture.entity.properties,
                },
                "entity_type": {
                    "id": capture.entity_type.id,
                    "name": capture.entity_type.name,
                    "class_name": capture.entity_type.class_name,
                },
                "role": role_for_app(capture, fields),
                "fields": fields,
                "methods": [method.model_dump(mode="json") for method in capture.methods],
                "runtime_params": params,
                "probes": probes,
            }
        )
    return apps


def find_app(apps: list[dict[str, Any]], role: str) -> dict[str, Any] | None:
    return next((app for app in apps if app["role"] == role), None)


def build_edges(apps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cpt_locator = find_app(apps, "cpt_locator")
    pile_capacity = find_app(apps, "pile_capacity_cpt")
    turbine = find_app(apps, "turbine_load_source")
    foundation = find_app(apps, "foundation_scia")
    rebar = find_app(apps, "reinforced_concrete_section")
    edges: list[dict[str, Any]] = []
    if cpt_locator and pile_capacity:
        edges.append(
            {
                "from": cpt_locator["role"],
                "to": pile_capacity["role"],
                "status": "candidate_file_bridge",
                "mapping": "download_cpt_xml output -> tab_input.sec_file.cpt_file",
                "note": "The source produces a signed XML download. The downstream expects a VIKTOR file id, so the bridge needs a file upload/registration step.",
            }
        )
    if turbine and foundation:
        edges.append(
            {
                "from": turbine["role"],
                "to": foundation["role"],
                "status": "proven_data_mapping",
                "mapping": {
                    "base_diameter": "step_geo.sec_mast.mast_diameter",
                    "base_vert_force": "step_geo.sec_mast.mast_vertical_load",
                    "base_horiz_force": "step_geo.sec_mast.mast_horizontal_load",
                    "base_moment": "step_geo.sec_mast.mast_moment",
                },
            }
        )
    if foundation and pile_capacity:
        edges.append(
            {
                "from": foundation["role"],
                "to": pile_capacity["role"],
                "status": "blocked_until_scia_results",
                "mapping": "view_pile_reactions max compression -> tab_input.sec_load.design_load",
                "note": "Current SCIA result views fail because step_analysis.esa_file is null.",
            }
        )
        edges.append(
            {
                "from": pile_capacity["role"],
                "to": foundation["role"],
                "status": "candidate_iterative_update",
                "mapping": {
                    "req_length_item": "step_geo.sec_piles.pile_length",
                    "diameter_item": "step_geo.sec_piles.pile_diameter",
                },
                "note": "This is an iterative sizing edge, not a simple one-way pipeline.",
            }
        )
    if foundation and rebar:
        edges.append(
            {
                "from": foundation["role"],
                "to": rebar["role"],
                "status": "blocked_until_scia_results",
                "mapping": "view_2d_internal_forces envelopes -> tab_loading.combinations[].M_Ed/N_Ed",
                "note": "The rebar app is ready, but the upstream SCIA data is currently unavailable.",
            }
        )
    return edges


BRIDGE_CODE = r'''
from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from typing import Any

import requests

TOKEN = (os.getenv("TOKEN_VK_APP") or os.getenv("VIKTOR_TOKEN") or "").strip()
if not TOKEN:
    raise RuntimeError("Set TOKEN_VK_APP or VIKTOR_TOKEN.")


class ViktorClient:
    def __init__(self, api_base: str) -> None:
        self.api_base = api_base.rstrip("/")
        self.headers = {"Authorization": f"Bearer {TOKEN}"}
        self.json_headers = {**self.headers, "Content-Type": "application/json"}

    def get(self, path_or_url: str, params: dict[str, Any] | None = None) -> Any:
        url = path_or_url if path_or_url.startswith("http") else f"{self.api_base}/{path_or_url.lstrip('/')}"
        response = requests.get(url, headers=self.headers, params=params, timeout=(5, 120))
        response.raise_for_status()
        return response.json()

    def post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        response = requests.post(
            f"{self.api_base}/{path.lstrip('/')}",
            headers=self.json_headers,
            json=payload or {},
            timeout=(5, 120),
        )
        response.raise_for_status()
        return response.json()

    def entity_properties(self, workspace_id: int, entity_id: int) -> dict[str, Any]:
        entity = self.get(
            f"workspaces/{workspace_id}/entities/{entity_id}/",
            params={"properties": "true", "clean_params": "true", "param_types": "true"},
        )
        return entity.get("properties") or {}

    def run_method(
        self,
        workspace_id: int,
        entity_id: int,
        method_name: str,
        params: dict[str, Any],
        max_seconds: int = 180,
    ) -> dict[str, Any]:
        created = self.post(
            f"workspaces/{workspace_id}/entities/{entity_id}/jobs/",
            {
                "method_name": method_name,
                "params": params,
                "poll_result": False,
                "timeout": 86400,
            },
        )
        if not created.get("url"):
            return created
        deadline = time.monotonic() + max_seconds
        while time.monotonic() < deadline:
            job = self.get(created["url"])
            if job.get("status") in {"success", "failed", "cancelled", "error", "error_user", "error_timeout"}:
                return job
            time.sleep(1.0)
        raise TimeoutError(f"{method_name} did not finish in {max_seconds}s")


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in (overlay or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def flatten_data_items(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}

    def visit(item: dict[str, Any], group: str | None = None) -> None:
        children = item.get("children") or []
        next_group = item.get("key") or item.get("label") or group if children else group
        if not children and item.get("key"):
            out[str(item["key"])] = {
                "group": group,
                "label": item.get("label"),
                "value": item.get("value"),
                "unit": item.get("suffix") or "",
            }
        for child in children:
            visit(child, next_group)

    for item in items:
        visit(item)
    return out


API = "https://demo.viktor.ai/api"
client = ViktorClient(API)

TURBINE = {"workspace_id": 2544, "entity_id": 12042}
FOUNDATION = {"workspace_id": 2515, "entity_id": 12040}
PILE_CAPACITY = {"workspace_id": 2564, "entity_id": 12049}
REBAR = {"workspace_id": 2541, "entity_id": 12027}


def build_foundation_from_turbine() -> dict[str, Any]:
    turbine_params = client.entity_properties(**TURBINE)
    turbine_job = client.run_method(**TURBINE, method_name="view_turbine_data", params=turbine_params)
    turbine_data = flatten_data_items((turbine_job.get("result") or {}).get("data") or [])

    foundation_params = client.entity_properties(**FOUNDATION)
    foundation_params.setdefault("step_geo", {}).setdefault("sec_mast", {})
    mast = foundation_params["step_geo"]["sec_mast"]
    mast["mast_diameter"] = turbine_data["base_diameter"]["value"]
    mast["mast_vertical_load"] = turbine_data["base_vert_force"]["value"]
    mast["mast_horizontal_load"] = turbine_data["base_horiz_force"]["value"]
    mast["mast_moment"] = turbine_data["base_moment"]["value"]
    return foundation_params


def run_workflow() -> dict[str, Any]:
    log: list[dict[str, Any]] = []
    foundation_params = build_foundation_from_turbine()

    if not foundation_params.get("step_analysis", {}).get("esa_file"):
        log.append({
            "node": "foundation_scia",
            "status": "blocked",
            "reason": "Missing step_analysis.esa_file. SCIA result methods cannot produce pile reactions or internal forces yet.",
            "prepared_params": foundation_params,
        })
        return {"status": "partial", "log": log}

    reactions = client.run_method(**FOUNDATION, method_name="view_pile_reactions", params=foundation_params)
    reaction_data = flatten_data_items((reactions.get("result") or {}).get("data") or [])
    # TODO: choose the exact max compression key from the real pile reaction table/data.
    max_compression_kn = max(
        value["value"]
        for value in reaction_data.values()
        if isinstance(value.get("value"), (int, float))
    )

    pile_params = client.entity_properties(**PILE_CAPACITY)
    pile_params.setdefault("tab_input", {}).setdefault("sec_load", {})["design_load"] = max_compression_kn
    pile_job = client.run_method(**PILE_CAPACITY, method_name="view_required_depth", params=pile_params)
    pile_data = flatten_data_items((pile_job.get("result") or {}).get("data") or [])

    foundation_params.setdefault("step_geo", {}).setdefault("sec_piles", {})
    foundation_params["step_geo"]["sec_piles"]["pile_length"] = pile_data["req_length_item"]["value"]

    internal_forces = client.run_method(**FOUNDATION, method_name="view_2d_internal_forces", params=foundation_params)
    # TODO: map real force envelope rows once the SCIA view succeeds.
    rebar_params = client.entity_properties(**REBAR)
    rebar_params.setdefault("tab_loading", {})["combinations"] = [
        {"label": "SCIA envelope +", "M_Ed": 0, "N_Ed": 0},
        {"label": "SCIA envelope -", "M_Ed": 0, "N_Ed": 0},
    ]
    rebar_job = client.run_method(**REBAR, method_name="view_optimise", params=rebar_params)

    return {
        "status": "success",
        "foundation_params": foundation_params,
        "pile_capacity": pile_job.get("status"),
        "internal_forces": internal_forces.get("status"),
        "rebar": rebar_job.get("status"),
        "log": log,
    }


if __name__ == "__main__":
    print(json.dumps(run_workflow(), indent=2, default=str))
'''


def markdown_for(apps: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    role_order = [
        "cpt_locator",
        "pile_capacity_cpt",
        "turbine_load_source",
        "foundation_scia",
        "reinforced_concrete_section",
    ]
    role_to_app = {app["role"]: app for app in apps}

    lines: list[str] = []
    lines.append("# VIKTOR Multi-App Workflow Analysis")
    lines.append("")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")
    lines.append("## How the agent knows the REST API")
    lines.append("")
    lines.append("- The system prompt tells the single agent to inspect VIKTOR URLs, resolve entities, preserve saved params, and generate propagation code.")
    lines.append("- The typed tools use the VIKTOR REST client in `app/viktor_api_tool/client.py` and discovery logic in `app/viktor_api_tool/discovery.py`.")
    lines.append("- The local shell tool uses `app/viktor_api_tool/local_shell_skill/SKILL.md`; it exposes `TOKEN_VK_APP` to Python/curl but redacts it from logs.")
    lines.append("- The token used here is the VIKTOR API token from `TOKEN_VK_APP` or `VIKTOR_TOKEN`, not the OpenAI key.")
    lines.append("")
    lines.append("## Proposed workflow")
    lines.append("")
    lines.append("```mermaid")
    lines.append("flowchart LR")
    lines.append('  A["CPT locator\\n2563 / entity 12048"] -->|download CPT XML; file bridge needed| B["Pile capacity from CPT\\n2564 / entity 12049"]')
    lines.append('  C["Turbine load source\\n2544 / entity 12042"] -->|base diameter, vertical, horizontal, moment| D["Foundation / SCIA\\n2515 / entity 12040"]')
    lines.append('  D -->|pile reactions; currently blocked by missing ESA| B')
    lines.append('  B -->|required pile length/diameter candidate| D')
    lines.append('  D -->|2D internal forces; currently blocked by missing ESA| E["RC rebar section\\n2541 / entity 12027"]')
    lines.append("```")
    lines.append("")
    lines.append("## Edges")
    lines.append("")
    for edge in edges:
        lines.append(f"- `{edge['from']}` -> `{edge['to']}`: `{edge['status']}`")
        lines.append(f"  - Mapping: `{edge['mapping']}`" if isinstance(edge["mapping"], str) else f"  - Mapping: {json.dumps(edge['mapping'])}")
        if edge.get("note"):
            lines.append(f"  - Note: {edge['note']}")
    lines.append("")
    lines.append("## App inspection")
    lines.append("")
    for role in role_order:
        app = role_to_app.get(role)
        if not app:
            continue
        lines.append(f"### {role}")
        lines.append("")
        lines.append(f"- URL: {app['url']}")
        lines.append(f"- Workspace/entity: `{app['workspace_id']}` / `{app['entity_id']}`")
        lines.append(f"- Entity name: `{app['entity']['name']}`")
        lines.append(f"- Entity type id/name: `{app['entity']['entity_type']}` / `{app['entity']['entity_type_name']}`")
        lines.append("- Saved properties:")
        lines.append(json_block(app["entity"]["properties"]))
        lines.append("- Input fields:")
        for field in app["fields"]:
            default = json.dumps(field.get("default"), ensure_ascii=False)
            lines.append(f"  - `{field['path']}` ({field['type']}): {field.get('label')} | default={default}")
        lines.append("- Methods:")
        for method in app["methods"]:
            lines.append(
                f"  - `{method['method_name']}`: {method.get('method_classification')} "
                f"/ expected={method.get('expected_result_kind')} / label={method.get('label')}"
            )
        lines.append("- Probes:")
        for name, probe in app["probes"].items():
            lines.append(f"  - `{name}`: status=`{probe.get('status')}`, kind=`{probe.get('result_kind')}`")
            flat = probe.get("flattened_data") or {}
            if flat:
                lines.append("    - Flattened output keys:")
                for key, value in list(flat.items())[:30]:
                    lines.append(
                        f"      - `{key}` = `{value.get('value')}` {value.get('unit') or ''} "
                        f"({value.get('label')})"
                    )
            if probe.get("download"):
                lines.append(f"    - Download: {probe['download']}")
            if probe.get("error"):
                lines.append(f"    - Error: {probe['error']}")
        lines.append("")
    lines.append("## Blockers")
    lines.append("")
    foundation = role_to_app.get("foundation_scia")
    if foundation:
        esa_file = (
            foundation["entity"]["properties"]
            .get("step_analysis", {})
            .get("esa_file")
        )
        if not esa_file:
            lines.append("- `foundation_scia.step_analysis.esa_file` is null. SCIA result methods are blocked until an `.esa` template is uploaded or otherwise made available.")
            lines.append("- Because of that, `view_pile_reactions`, `view_results`, and `view_2d_internal_forces` currently fail.")
    lines.append("- `cpt_locator.download_cpt_xml` returns a signed XML download URL. To feed `pile_capacity_cpt.tab_input.sec_file.cpt_file`, the bridge still needs a VIKTOR file upload/registration step that returns a file id.")
    lines.append("")
    lines.append("## Generic propagation rule")
    lines.append("")
    lines.append("1. Re-read every entity before each run.")
    lines.append("2. Build downstream params as `defaults + latest saved entity properties`.")
    lines.append("3. Re-run upstream output methods.")
    lines.append("4. Apply only validated mappings.")
    lines.append("5. Preserve downstream-only fields.")
    lines.append("6. Log blocked branches and continue independent branches.")
    lines.append("")
    lines.append("## Bridge code skeleton")
    lines.append("")
    lines.append("```python")
    lines.append(BRIDGE_CODE.strip())
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze VIKTOR app URLs and create a workflow markdown artifact.")
    parser.add_argument("urls", nargs="*", default=DEFAULT_URLS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    ensure_token()
    apps = inspect_apps(args.urls)
    edges = build_edges(apps)
    artifact = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "urls": args.urls,
        "apps": apps,
        "edges": edges,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = args.output_dir / "latest.json"
    md_path = args.output_dir / "workflow_analysis.md"
    raw_path.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(markdown_for(apps, edges), encoding="utf-8")
    print(
        json.dumps(
            {
                "markdown": str(md_path),
                "raw_json": str(raw_path),
                "app_count": len(apps),
                "edge_count": len(edges),
                "roles": [app["role"] for app in apps],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
