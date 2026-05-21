from __future__ import annotations

import base64
import json
import os
import shlex
from typing import Any, Literal

import viktor as vkt
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from .client import ViktorApiClient, app_url_from_target, parse_app_url, resolve_target
from .code_editor import load_code_files, save_code_files, set_code_editor_visibility
from .discovery import capture_app, compact_capture, detect_result_kind
from .local_shell import run_local_viktor_shell_commands

load_dotenv()


def _storage_set_json(key: str, payload: Any) -> None:
    vkt.Storage().set(
        key,
        data=vkt.File.from_data(json.dumps(payload)),
        scope="entity",
    )


def _target_kwargs(payload: Any) -> dict[str, Any]:
    return {
        "app_url": payload.app_url,
        "workspace_id": payload.workspace_id,
        "entity_id": payload.entity_id,
        "api_base": payload.api_base,
    }


class ViktorAppTargetInput(BaseModel):
    app_url: str | None = Field(
        default=None,
        description="Full VIKTOR editor URL, for example https://demo.viktor.ai/workspaces/2141/app/editor/11536.",
    )
    workspace_id: int | None = Field(default=None, description="VIKTOR workspace id.")
    entity_id: int | None = Field(default=None, description="VIKTOR entity id.")
    api_base: str | None = Field(
        default=None,
        description="VIKTOR API base or environment, for example https://demo.viktor.ai/api or demo.",
    )


class InspectViktorAppArgs(ViktorAppTargetInput):
    params: dict[str, Any] | None = Field(
        default=None,
        description="Optional params to resolve dynamic parametrization. Defaults to saved entity properties.",
    )
    include_raw: bool = Field(
        default=False,
        description="Include raw API payloads in the returned JSON.",
    )
    probe_output_methods: bool = Field(
        default=False,
        description="Run discovered DataView/TableView methods with the default-plus-saved payload.",
    )
    method_names: list[str] = Field(
        default_factory=list,
        description="Optional exact method names to probe instead of all data/table methods.",
    )


async def inspect_viktor_app_func(_ctx: Any, args: str) -> str:
    payload = InspectViktorAppArgs.model_validate_json(args)
    try:
        target = resolve_target(**_target_kwargs(payload))
        client = ViktorApiClient(api_base=target.api_base)
        capture = capture_app(client=client, target=target, params=payload.params)
        result = compact_capture(capture, include_raw=payload.include_raw)
    except Exception as exc:
        response = {
            "status": "error",
            "tool": "inspect_viktor_app",
            "input": payload.model_dump(mode="json"),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "guidance": (
                "Do not stop the workflow. Resolve the URL/entity if possible, "
                "or mark this app/edge as blocked or unknown."
            ),
        }
        _storage_set_json("latest_viktor_app_capture_error", response)
        return json.dumps(response, indent=2)

    if payload.probe_output_methods:
        candidate_params = capture.default_plus_saved_payload.params or capture.entity.properties
        method_filter = set(payload.method_names)
        probe_methods = [
            method
            for method in capture.methods
            if (not method_filter and method in capture.data_methods)
            or method.method_name in method_filter
        ]
        probes: list[dict[str, Any]] = []
        for method in probe_methods:
            try:
                job = client.create_job(
                    target,
                    method_name=method.method_name,
                    params=candidate_params,
                )
                result_kind, result_keys, result_summary = detect_result_kind(job.result)
                probes.append(
                    {
                        "method_name": method.method_name,
                        "status": job.status,
                        "expected_result_kind": method.expected_result_kind,
                        "actual_result_kind": result_kind,
                        "result_keys": result_keys,
                        "result_summary": result_summary,
                    }
                )
            except Exception as exc:
                probes.append({"method_name": method.method_name, "error": str(exc)})
        result["method_probes"] = probes

    storage_key = f"viktor_app_capture_{target.workspace_id}_{target.entity_id}"
    _storage_set_json(storage_key, result)
    _storage_set_json("latest_viktor_app_capture", result)
    return json.dumps(result, indent=2)


def inspect_viktor_app_tool() -> Any:
    from agents import FunctionTool

    return FunctionTool(
        name="inspect_viktor_app",
        description=(
            "Fetch a VIKTOR app's entity properties, param types, resolved parametrization, "
            "default payload candidates, available methods, and DataView/TableView methods."
        ),
        params_json_schema=InspectViktorAppArgs.model_json_schema(),
        on_invoke_tool=inspect_viktor_app_func,
        strict_json_schema=False,
    )


class RunViktorAppMethodArgs(ViktorAppTargetInput):
    method_name: str = Field(..., min_length=1, description="Controller method to run.")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Method params payload.",
    )
    method_type: str | None = Field(default=None, description="Optional VIKTOR method type.")
    editor_session: str | None = Field(
        default=None,
        description="Optional editor session UUID for jobs that need session context.",
    )
    timeout: int = Field(default=86400, ge=1, le=86400)
    download_result: bool = Field(
        default=True,
        description="Download JSON/text result when the job returns result.download.url.",
    )


async def run_viktor_app_method_func(_ctx: Any, args: str) -> str:
    payload = RunViktorAppMethodArgs.model_validate_json(args)
    try:
        target = resolve_target(**_target_kwargs(payload))
        client = ViktorApiClient(api_base=target.api_base)
        job = client.create_job(
            target,
            method_name=payload.method_name,
            params=payload.params,
            method_type=payload.method_type,
            editor_session=payload.editor_session,
            timeout=payload.timeout,
        )
    except Exception as exc:
        response = {
            "status": "error",
            "tool": "run_viktor_app_method",
            "input": payload.model_dump(mode="json"),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "guidance": (
                "Do not stop the workflow. Mark this method, output, or edge as "
                "blocked if the failure is caused by a missing file, worker, "
                "integration, credential, or other non-agent-facing requirement."
            ),
        }
        _storage_set_json("latest_viktor_job_result_error", response)
        return json.dumps(response, indent=2)
    result_kind, result_keys, result_summary = detect_result_kind(job.result)
    response: dict[str, Any] = {
        "app_url": app_url_from_target(target),
        "method_name": payload.method_name,
        "status": job.status,
        "uid": job.uid,
        "kind": job.kind,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "log_download_url": job.log_download_url,
        "actual_result_kind": result_kind,
        "result_keys": result_keys,
        "result_summary": result_summary,
        "result": job.result.model_dump(mode="json") if job.result else None,
    }
    if payload.download_result and job.download_url:
        response["download_result"] = client.download_result(job.download_url)
    _storage_set_json("latest_viktor_job_result", response)
    return json.dumps(response, indent=2)


def run_viktor_app_method_tool() -> Any:
    from agents import FunctionTool

    return FunctionTool(
        name="run_viktor_app_method",
        description=(
            "Create and poll a VIKTOR app job with a provided params payload. "
            "Reads TOKEN_VK_APP or VIKTOR_TOKEN from the app environment."
        ),
        params_json_schema=RunViktorAppMethodArgs.model_json_schema(),
        on_invoke_tool=run_viktor_app_method_func,
        strict_json_schema=False,
    )


class CreateViktorSiblingEntityArgs(ViktorAppTargetInput):
    name: str | None = Field(
        default=None,
        description="Name for the new sibling entity. Defaults to '<source name> workflow copy'.",
    )
    properties: dict[str, Any] | None = Field(
        default=None,
        description="Properties for the new entity. Defaults to the source entity properties.",
    )
    entity_type: int | None = Field(
        default=None,
        description="Entity type id. Defaults to the source entity type.",
    )
    parent_entity_id: int | None = Field(
        default=None,
        description="Explicit parent entity id. Defaults to the source entity parent when available.",
    )


async def create_viktor_sibling_entity_func(_ctx: Any, args: str) -> str:
    payload = CreateViktorSiblingEntityArgs.model_validate_json(args)
    target = resolve_target(**_target_kwargs(payload))
    client = ViktorApiClient(api_base=target.api_base)
    source = client.get_entity(target)
    name = payload.name or f"{source.name} workflow copy"
    created = client.create_sibling_entity(
        target,
        name=name,
        properties=payload.properties,
        entity_type=payload.entity_type,
        parent_entity_id=payload.parent_entity_id,
    )
    created_target = target.model_copy(update={"entity_id": created.id})
    response = {
        "source_app_url": app_url_from_target(target),
        "created_app_url": app_url_from_target(created_target),
        "entity": created.model_dump(mode="json"),
    }
    return json.dumps(response, indent=2)


def create_viktor_sibling_entity_tool() -> Any:
    from agents import FunctionTool

    return FunctionTool(
        name="create_viktor_sibling_entity",
        description=(
            "Create a new entity next to a source VIKTOR entity, copying the source "
            "entity type and properties unless replacements are provided."
        ),
        params_json_schema=CreateViktorSiblingEntityArgs.model_json_schema(),
        on_invoke_tool=create_viktor_sibling_entity_func,
        strict_json_schema=False,
    )


class GenerateViktorBridgeCodeArgs(BaseModel):
    source_app_url: str = Field(..., description="Source VIKTOR editor URL.")
    target_app_url: str = Field(..., description="Target VIKTOR editor URL.")
    source_method_name: str | None = Field(
        default=None,
        description="Source method to run. Defaults to first DataView/TableView method, then first discovered method.",
    )
    target_method_name: str | None = Field(
        default=None,
        description="Target method to run. Defaults to first discovered target method.",
    )
    source_params: dict[str, Any] | None = Field(
        default=None,
        description="Source method params. Defaults to source defaults plus saved properties.",
    )
    target_params: dict[str, Any] | None = Field(
        default=None,
        description="Target base params. Defaults to target defaults plus saved properties.",
    )
    mapping_notes: str | None = Field(
        default=None,
        description="Natural-language notes for mapping source output into target input.",
    )
    file_name: str = Field(default="viktor_app_bridge.py")


def _pick_method(capture: Any, requested: str | None, *, prefer_data: bool) -> str:
    if requested:
        return requested
    candidates = capture.data_methods if prefer_data and capture.data_methods else capture.methods
    if not candidates:
        raise ValueError("No callable methods were discovered.")
    return candidates[0].method_name


def _build_bridge_code(
    *,
    source_capture: Any,
    target_capture: Any,
    source_app_url: str,
    target_app_url: str,
    source_method_name: str,
    target_method_name: str,
    source_params: dict[str, Any],
    target_params: dict[str, Any],
    mapping_notes: str | None,
) -> str:
    source_target = parse_app_url(source_app_url)
    target_target = parse_app_url(target_app_url)
    return f'''"""
Bridge script generated by the VIKTOR agent.

Source: {source_app_url}
Target: {target_app_url}
Mapping notes: {mapping_notes or "Map source DataView/TableView output into the target parametrization payload."}
"""

from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from typing import Any

import requests


TOKEN = (os.getenv("TOKEN_VK_APP") or os.getenv("VIKTOR_TOKEN") or "").strip()
if not TOKEN:
    raise RuntimeError("Set TOKEN_VK_APP or VIKTOR_TOKEN before running this script.")

SOURCE_API_BASE = {source_target.api_base!r}
SOURCE_WORKSPACE_ID = {source_target.workspace_id}
SOURCE_ENTITY_ID = {source_target.entity_id}
SOURCE_METHOD_NAME = {source_method_name!r}

TARGET_API_BASE = {target_target.api_base!r}
TARGET_WORKSPACE_ID = {target_target.workspace_id}
TARGET_ENTITY_ID = {target_target.entity_id}
TARGET_METHOD_NAME = {target_method_name!r}

SOURCE_BASE_PARAMS = {json.dumps(source_params, indent=2)}
TARGET_BASE_PARAMS = {json.dumps(target_params, indent=2)}


class ViktorRestClient:
    def __init__(self, api_base: str, token: str) -> None:
        self.api_base = api_base.rstrip("/")
        self.headers = {{"Authorization": f"Bearer {{token}}"}}
        self.json_headers = {{**self.headers, "Content-Type": "application/json"}}
        self.timeout = (5.0, 120.0)

    def post_json(self, path: str, payload: dict[str, Any]) -> Any:
        response = requests.post(
            f"{{self.api_base}}/{{path.lstrip('/')}}",
            headers=self.json_headers,
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return response.json()

    def get_json(self, url: str) -> Any:
        response = requests.get(url, headers=self.headers, timeout=self.timeout)
        response.raise_for_status()
        return response.json()

    def get_entity_properties(self, *, workspace_id: int, entity_id: int) -> dict[str, Any]:
        response = requests.get(
            f"{{self.api_base}}/workspaces/{{workspace_id}}/entities/{{entity_id}}/",
            headers=self.headers,
            params={{"properties": "true", "clean_params": "true"}},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        return payload.get("properties") or {{}}

    def run_method(
        self,
        *,
        workspace_id: int,
        entity_id: int,
        method_name: str,
        params: dict[str, Any],
        max_poll_seconds: int = 180,
    ) -> dict[str, Any]:
        job = self.post_json(
            f"workspaces/{{workspace_id}}/entities/{{entity_id}}/jobs/",
            {{
                "method_name": method_name,
                "params": params,
                "poll_result": False,
            }},
        )
        if "url" not in job:
            return job

        deadline = time.monotonic() + max_poll_seconds
        while time.monotonic() < deadline:
            status = self.get_json(job["url"])
            if status.get("status") in {{"success", "failed", "cancelled", "error", "error_user", "error_timeout"}}:
                return status
            time.sleep(1.0)
        raise TimeoutError(f"Job did not finish within {{max_poll_seconds}} seconds.")


def deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in (overlay or {{}}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def extract_result_payload(job: dict[str, Any]) -> Any:
    result = job.get("result") or job.get("content") or {{}}
    if "data" in result:
        return result["data"]
    if "table" in result:
        return result["table"]
    if "download" in result:
        return result["download"]
    return result


def build_target_params(source_output: Any, target_base_params: dict[str, Any]) -> dict[str, Any]:
    params = deepcopy(target_base_params)

    # Propagation rule:
    # - Recompute this payload every run from the latest source params/output.
    # - Start from the latest downstream saved values merged over generated defaults.
    # - Preserve downstream-only fields from that live downstream base.
    # - Apply only mappings that have been validated for this pair of apps.
    # - Record blocked or unresolved mappings instead of inventing values.
    blocked_edges: list[dict[str, Any]] = []

    # TODO: Replace this generic mapping with the validated mapping between apps.
    # Source methods discovered:
    # {", ".join(method.method_name for method in source_capture.methods)}
    # Target fields are visible in TARGET_BASE_PARAMS and the target parametrization capture.
    # Example:
    # params["target_section"]["target_field"] = source_output["source_key"]
    if params.get("step_analysis", {{}}).get("esa_file") in (None, "", {{}}):
        blocked_edges.append({{
            "path": "step_analysis.esa_file",
            "reason": "Missing downstream file/integration input; preserve value and skip dependent SCIA result execution.",
        }})

    params["_workflow_meta"] = {{
        "propagated_from": {{
            "workspace_id": SOURCE_WORKSPACE_ID,
            "entity_id": SOURCE_ENTITY_ID,
            "method_name": SOURCE_METHOD_NAME,
        }},
        "blocked_edges": blocked_edges,
    }}
    return params


def main() -> None:
    source_client = ViktorRestClient(SOURCE_API_BASE, TOKEN)
    target_client = ViktorRestClient(TARGET_API_BASE, TOKEN)

    source_params = deep_merge(
        SOURCE_BASE_PARAMS,
        source_client.get_entity_properties(
            workspace_id=SOURCE_WORKSPACE_ID,
            entity_id=SOURCE_ENTITY_ID,
        ),
    )
    target_base_params = deep_merge(
        TARGET_BASE_PARAMS,
        target_client.get_entity_properties(
            workspace_id=TARGET_WORKSPACE_ID,
            entity_id=TARGET_ENTITY_ID,
        ),
    )

    source_job = source_client.run_method(
        workspace_id=SOURCE_WORKSPACE_ID,
        entity_id=SOURCE_ENTITY_ID,
        method_name=SOURCE_METHOD_NAME,
        params=source_params,
    )
    source_output = extract_result_payload(source_job)
    target_params = build_target_params(source_output, target_base_params)

    blocked_edges = target_params.pop("_workflow_meta", {{}}).get("blocked_edges", [])
    if blocked_edges:
        print(json.dumps({{
            "source_status": source_job.get("status"),
            "target_status": "blocked",
            "blocked_edges": blocked_edges,
            "target_params_preview": target_params,
        }}, indent=2))
        return

    target_job = target_client.run_method(
        workspace_id=TARGET_WORKSPACE_ID,
        entity_id=TARGET_ENTITY_ID,
        method_name=TARGET_METHOD_NAME,
        params=target_params,
    )

    print(json.dumps({{
        "source_status": source_job.get("status"),
        "target_status": target_job.get("status"),
        "target_result": target_job.get("result") or target_job.get("content"),
    }}, indent=2))


if __name__ == "__main__":
    main()
'''


async def generate_viktor_bridge_code_func(_ctx: Any, args: str) -> str:
    payload = GenerateViktorBridgeCodeArgs.model_validate_json(args)
    source_target = parse_app_url(payload.source_app_url)
    target_target = parse_app_url(payload.target_app_url)

    source_capture = capture_app(
        client=ViktorApiClient(api_base=source_target.api_base),
        target=source_target,
        params=payload.source_params,
    )
    target_capture = capture_app(
        client=ViktorApiClient(api_base=target_target.api_base),
        target=target_target,
        params=payload.target_params,
    )
    source_method_name = _pick_method(
        source_capture,
        payload.source_method_name,
        prefer_data=True,
    )
    target_method_name = _pick_method(
        target_capture,
        payload.target_method_name,
        prefer_data=False,
    )
    source_params = payload.source_params or source_capture.default_plus_saved_payload.params
    target_params = payload.target_params or target_capture.default_plus_saved_payload.params
    code = _build_bridge_code(
        source_capture=source_capture,
        target_capture=target_capture,
        source_app_url=payload.source_app_url,
        target_app_url=payload.target_app_url,
        source_method_name=source_method_name,
        target_method_name=target_method_name,
        source_params=source_params,
        target_params=target_params,
        mapping_notes=payload.mapping_notes,
    )
    summary = {
        "source": {
            "app_url": payload.source_app_url,
            "method": source_method_name,
            "data_methods": [method.model_dump(mode="json") for method in source_capture.data_methods],
        },
        "target": {
            "app_url": payload.target_app_url,
            "method": target_method_name,
            "methods": [method.model_dump(mode="json") for method in target_capture.methods],
        },
        "mapping_notes": payload.mapping_notes,
    }
    save_code_files(
        {
            payload.file_name: code,
            "workflow_summary.json": json.dumps(summary, indent=2),
        },
        show=True,
    )
    return json.dumps(
        {
            "saved_files": [payload.file_name, "workflow_summary.json"],
            "source_method_name": source_method_name,
            "target_method_name": target_method_name,
            "code_editor": "show",
        },
        indent=2,
    )


def generate_viktor_bridge_code_tool() -> Any:
    from agents import FunctionTool

    return FunctionTool(
        name="generate_viktor_bridge_code",
        description=(
            "Inspect two VIKTOR apps and generate a Python bridge script that runs a "
            "source method, maps its output, and runs a target app method. Saves the "
            "script to the Monaco code WebView."
        ),
        params_json_schema=GenerateViktorBridgeCodeArgs.model_json_schema(),
        on_invoke_tool=generate_viktor_bridge_code_func,
        strict_json_schema=False,
    )


class SaveWorkflowCodeArgs(BaseModel):
    files: dict[str, str] = Field(
        default_factory=dict,
        description="Files to show in the Monaco workflow code viewer.",
    )
    show: bool = Field(default=True, description="Show the code editor after saving.")


async def save_workflow_code_func(_ctx: Any, args: str) -> str:
    payload = SaveWorkflowCodeArgs.model_validate_json(args)
    save_code_files(payload.files, show=payload.show)
    return json.dumps({"saved_files": sorted(payload.files), "code_editor": "show" if payload.show else "unchanged"})


def save_workflow_code_tool() -> Any:
    from agents import FunctionTool

    return FunctionTool(
        name="save_workflow_code",
        description=(
            "Save generated code or notes into the Monaco workflow code WebView. "
            "Pass files as an object mapping filename to complete file contents, "
            "for example {'workflow.py': '...'} or {'notes.md': '...'}."
        ),
        params_json_schema=SaveWorkflowCodeArgs.model_json_schema(),
        on_invoke_tool=save_workflow_code_func,
        strict_json_schema=False,
    )


class ShowHideCodeEditorArgs(BaseModel):
    action: Literal["show", "hide"] = Field(..., description="Show or hide the code editor WebView.")


async def show_hide_code_editor_func(_ctx: Any, args: str) -> str:
    payload = ShowHideCodeEditorArgs.model_validate_json(args)
    set_code_editor_visibility(payload.action)
    return f"Code editor visibility changed to {payload.action}."


def show_hide_code_editor_tool() -> Any:
    from agents import FunctionTool

    return FunctionTool(
        name="show_hide_code_editor",
        description="Show or hide the Monaco workflow code WebView.",
        params_json_schema=ShowHideCodeEditorArgs.model_json_schema(),
        on_invoke_tool=show_hide_code_editor_func,
        strict_json_schema=False,
    )


class RunWorkflowCodeArgs(BaseModel):
    file_name: str | None = Field(
        default=None,
        description="Saved Monaco Python file to execute. Defaults to the first .py file.",
    )
    timeout_ms: int = Field(
        default=120_000,
        ge=1_000,
        le=120_000,
        description="Maximum execution time for the sandboxed shell command.",
    )
    app_urls: list[str] = Field(
        default_factory=list,
        description="Optional VIKTOR URLs used to allow additional REST domains in the sandbox.",
    )


def _select_python_workflow_file(files: dict[str, str], requested: str | None) -> tuple[str, str]:
    if requested:
        if requested not in files:
            raise ValueError(f"Saved code file not found: {requested}")
        if not requested.endswith(".py"):
            raise ValueError(f"Saved code file is not a Python file: {requested}")
        return requested, files[requested]

    for file_name, code in sorted(files.items()):
        if file_name.endswith(".py"):
            return file_name, code

    raise ValueError("No saved Python workflow file was found in the Monaco code storage.")


async def run_workflow_code_func(_ctx: Any, args: str) -> str:
    payload = RunWorkflowCodeArgs.model_validate_json(args)
    files = load_code_files()
    file_name, code = _select_python_workflow_file(files, payload.file_name)
    encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
    safe_file_name = os.path.basename(file_name) or "workflow.py"
    launcher = f"""
import base64
import pathlib
import runpy

code = base64.b64decode({encoded!r}).decode("utf-8")
path = pathlib.Path({safe_file_name!r})
path.write_text(code, encoding="utf-8")
runpy.run_path(str(path), run_name="__main__")
""".strip()
    command = f"python -c {shlex.quote(launcher)}"
    result = await run_local_viktor_shell_commands(
        commands=[command],
        app_urls=payload.app_urls,
        timeout_ms=payload.timeout_ms,
    )
    response = {
        "file_name": file_name,
        "working_directory": result.get("working_directory"),
        "allowed_domains": result.get("allowed_domains"),
        "commands": result.get("commands", []),
    }
    _storage_set_json("latest_workflow_code_run", response)
    return json.dumps(response, indent=2)


def run_workflow_code_tool() -> Any:
    from agents import FunctionTool

    return FunctionTool(
        name="run_workflow_code",
        description=(
            "Execute a saved Python file from the Monaco workflow code WebView in "
            "the constrained local VIKTOR shell. The shell exposes TOKEN_VK_APP "
            "and permits Python requests to allowed VIKTOR REST domains."
        ),
        params_json_schema=RunWorkflowCodeArgs.model_json_schema(),
        on_invoke_tool=run_workflow_code_func,
        strict_json_schema=False,
    )


def get_viktor_api_tools() -> list[Any]:
    return [
        inspect_viktor_app_tool(),
        run_viktor_app_method_tool(),
        create_viktor_sibling_entity_tool(),
        generate_viktor_bridge_code_tool(),
        save_workflow_code_tool(),
        show_hide_code_editor_tool(),
        run_workflow_code_tool(),
    ]
