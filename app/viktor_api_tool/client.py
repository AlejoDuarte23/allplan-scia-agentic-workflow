from __future__ import annotations

import os
import re
import time
from typing import Any

import requests
from dotenv import load_dotenv

from .models import (
    AppTarget,
    EditorSessionResponse,
    EntityResponse,
    EntityTypeResponse,
    JobCreateResponse,
    JobResultPayload,
    JobStatusResponse,
    ParametrizationResponse,
)

APP_URL_PATTERN = re.compile(
    r"^https?://(?P<host>[^/]+)/workspaces/(?P<workspace_id>\d+)/app/editor/(?P<entity_id>\d+)(?:[/?#].*)?$"
)


def _load_env() -> None:
    load_dotenv()


def get_token() -> str:
    _load_env()
    token = (os.getenv("TOKEN_VK_APP") or os.getenv("VIKTOR_TOKEN") or "").strip()
    if not token:
        raise ValueError("Missing VIKTOR token. Set TOKEN_VK_APP or VIKTOR_TOKEN.")
    return token


def normalize_api_base(value: str | None = None) -> str:
    if value:
        raw = value.strip().rstrip("/")
    else:
        raw = (os.getenv("VIKTOR_API_BASE") or os.getenv("VIKTOR_ENVIRONMENT") or "demo").strip().rstrip("/")

    if raw.startswith("https://") or raw.startswith("http://"):
        base = raw
    elif raw.endswith(".viktor.ai"):
        base = f"https://{raw}"
    else:
        base = f"https://{raw}.viktor.ai"

    if not base.startswith("https://"):
        raise ValueError("VIKTOR API base must resolve to an HTTPS URL.")

    return base if base.endswith("/api") else f"{base}/api"


def ui_base_from_api_base(api_base: str) -> str:
    return api_base.rstrip("/").removesuffix("/api")


def app_url_from_target(target: AppTarget) -> str:
    return (
        f"{ui_base_from_api_base(target.api_base)}/workspaces/"
        f"{target.workspace_id}/app/editor/{target.entity_id}"
    )


def parse_app_url(app_url: str) -> AppTarget:
    match = APP_URL_PATTERN.match(app_url.strip())
    if not match:
        raise ValueError(f"Unsupported VIKTOR app URL: {app_url}")
    host = match.group("host")
    api_base = normalize_api_base(f"https://{host}/api")
    return AppTarget(
        api_base=api_base,
        workspace_id=int(match.group("workspace_id")),
        entity_id=int(match.group("entity_id")),
        app_url=app_url,
    )


def resolve_target(
    *,
    app_url: str | None = None,
    workspace_id: int | None = None,
    entity_id: int | None = None,
    api_base: str | None = None,
) -> AppTarget:
    if app_url:
        return parse_app_url(app_url)
    if workspace_id is None or entity_id is None:
        raise ValueError("Provide either app_url or both workspace_id and entity_id.")
    target = AppTarget(
        api_base=normalize_api_base(api_base),
        workspace_id=workspace_id,
        entity_id=entity_id,
    )
    target.app_url = app_url_from_target(target)
    return target


class ViktorApiClient:
    def __init__(
        self,
        *,
        api_base: str | None = None,
        token: str | None = None,
        connect_timeout: float = 5.0,
        read_timeout: float = 120.0,
        max_poll_seconds: int | None = None,
    ) -> None:
        self.api_base = normalize_api_base(api_base)
        self.token = (token or get_token()).strip()
        if not self.token:
            raise ValueError("Missing VIKTOR token.")
        self.timeout = (connect_timeout, read_timeout)
        self.max_poll_seconds = max_poll_seconds or int(
            os.getenv("VIKTOR_MAX_POLL_SECONDS", "180")
        )
        self.auth_headers = {"Authorization": f"Bearer {self.token}"}
        self.json_headers = {**self.auth_headers, "Content-Type": "application/json"}

    def _url(self, path: str) -> str:
        return f"{self.api_base.rstrip('/')}/{path.lstrip('/')}"

    def _check(self, response: requests.Response, *, action: str) -> None:
        if response.ok:
            return
        body = response.text[:500]
        raise RuntimeError(f"{action} failed (status={response.status_code}): {body}")

    def get_json(
        self,
        path_or_url: str,
        *,
        params: dict[str, Any] | None = None,
        action: str = "GET request",
    ) -> Any:
        url = path_or_url if path_or_url.startswith("http") else self._url(path_or_url)
        response = requests.get(
            url,
            headers=self.auth_headers,
            params=params,
            timeout=self.timeout,
        )
        self._check(response, action=action)
        return response.json()

    def post_json(
        self,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        action: str = "POST request",
    ) -> Any:
        response = requests.post(
            self._url(path),
            headers=self.json_headers,
            json=payload or {},
            timeout=self.timeout,
        )
        self._check(response, action=action)
        return response.json()

    def put_json(
        self,
        path: str,
        *,
        payload: dict[str, Any],
        action: str = "PUT request",
    ) -> Any:
        response = requests.put(
            self._url(path),
            headers=self.json_headers,
            json=payload,
            timeout=self.timeout,
        )
        self._check(response, action=action)
        return response.json()

    def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        url: str | None = self._url(path)
        page_params = params
        results: list[dict[str, Any]] = []
        while url:
            payload = self.get_json(url, params=page_params, action=f"List {path}")
            results.extend(payload.get("results", []))
            url = payload.get("next")
            page_params = None
        return results

    def get_entity(self, target: AppTarget) -> EntityResponse:
        payload = self.get_json(
            f"workspaces/{target.workspace_id}/entities/{target.entity_id}/",
            params={
                "properties": "true",
                "clean_params": "true",
                "param_types": "true",
            },
            action="Get entity",
        )
        return EntityResponse.model_validate(payload)

    def get_entity_type(
        self,
        *,
        workspace_id: int,
        entity_type_id: int,
    ) -> EntityTypeResponse:
        payload = self.get_json(
            f"workspaces/{workspace_id}/entity_types/{entity_type_id}/",
            action="Get entity type",
        )
        return EntityTypeResponse.model_validate(payload)

    def get_parent_entity(self, target: AppTarget) -> EntityResponse | None:
        try:
            payload = self.get_json(
                f"workspaces/{target.workspace_id}/entities/{target.entity_id}/parent/",
                action="Get parent entity",
            )
            return EntityResponse.model_validate(payload)
        except RuntimeError as exc:
            message = str(exc)
            if "status=404" in message or "status=403" in message:
                return None
            raise

    def create_editor_session(self, target: AppTarget) -> EditorSessionResponse:
        payload = self.post_json(
            f"workspaces/{target.workspace_id}/entities/{target.entity_id}/session/",
            action="Create editor session",
        )
        return EditorSessionResponse.model_validate(payload)

    def get_parametrization(
        self,
        target: AppTarget,
        *,
        editor_session: str,
        params: dict[str, Any] | None = None,
    ) -> ParametrizationResponse:
        payload = self.post_json(
            f"workspaces/{target.workspace_id}/entities/{target.entity_id}/parametrization/",
            payload={
                "editor_session": editor_session,
                "params": params or {},
            },
            action="Get parametrization",
        )
        return ParametrizationResponse.model_validate(payload)

    def create_child_entity(
        self,
        *,
        workspace_id: int,
        parent_entity_id: int | None,
        entity_type: int,
        name: str,
        properties: dict[str, Any],
    ) -> EntityResponse:
        path = (
            f"workspaces/{workspace_id}/entities/{parent_entity_id}/entities/"
            if parent_entity_id is not None
            else f"workspaces/{workspace_id}/entities/"
        )
        payload = self.post_json(
            path,
            payload={
                "entity_type": entity_type,
                "name": name,
                "properties": properties,
            },
            action="Create entity",
        )
        if isinstance(payload, list):
            if not payload:
                raise RuntimeError("Create entity returned an empty list.")
            payload = payload[0]
        return EntityResponse.model_validate(payload)

    def create_sibling_entity(
        self,
        target: AppTarget,
        *,
        name: str,
        properties: dict[str, Any] | None = None,
        entity_type: int | None = None,
        parent_entity_id: int | None = None,
    ) -> EntityResponse:
        source_entity = self.get_entity(target)
        parent = None if parent_entity_id is not None else self.get_parent_entity(target)
        effective_parent_id = parent_entity_id if parent_entity_id is not None else (parent.id if parent else None)
        return self.create_child_entity(
            workspace_id=target.workspace_id,
            parent_entity_id=effective_parent_id,
            entity_type=entity_type or source_entity.entity_type,
            name=name,
            properties=properties if properties is not None else source_entity.properties,
        )

    def update_entity(
        self,
        target: AppTarget,
        *,
        name: str,
        properties: dict[str, Any],
        message: str,
    ) -> EntityResponse:
        payload = self.put_json(
            f"workspaces/{target.workspace_id}/entities/{target.entity_id}/",
            payload={"name": name, "properties": properties, "message": message},
            action="Update entity",
        )
        return EntityResponse.model_validate(payload)

    def create_job(
        self,
        target: AppTarget,
        *,
        method_name: str,
        params: dict[str, Any],
        method_type: str | None = None,
        editor_session: str | None = None,
        timeout: int = 86400,
    ) -> JobStatusResponse:
        payload: dict[str, Any] = {
            "method_name": method_name,
            "params": params,
            "poll_result": False,
            "timeout": timeout,
        }
        if method_type:
            payload["method_type"] = method_type
        if editor_session:
            payload["editor_session"] = editor_session

        response_payload = self.post_json(
            f"workspaces/{target.workspace_id}/entities/{target.entity_id}/jobs/",
            payload=payload,
            action="Create job",
        )
        job_create = JobCreateResponse.model_validate(response_payload)

        if job_create.url:
            return self.poll_job(job_create.url)

        if job_create.status == "success":
            return JobStatusResponse(
                uid=job_create.uid,
                kind=job_create.kind or "result",
                status="success",
                result=(
                    JobResultPayload.model_validate(job_create.content)
                    if job_create.content
                    else None
                ),
            )

        if job_create.status:
            return JobStatusResponse(
                uid=job_create.uid,
                kind=job_create.kind,
                status=job_create.status,
                error={"message": job_create.error_message}
                if job_create.error_message
                else None,
            )

        raise RuntimeError(
            f"Unexpected job creation response: {job_create.model_dump(mode='json')}"
        )

    def poll_job(self, job_url: str) -> JobStatusResponse:
        deadline = time.monotonic() + self.max_poll_seconds
        sleep_s = 0.8
        while time.monotonic() < deadline:
            payload = self.get_json(job_url, action="Poll job")
            job = JobStatusResponse.model_validate(payload)

            if job.is_success() or job.is_failed():
                return job

            time.sleep(sleep_s)
            sleep_s = min(sleep_s * 1.5, 5.0)

        raise TimeoutError(f"Job did not finish within {self.max_poll_seconds} seconds.")

    def download_result(self, url: str) -> Any:
        response = requests.get(url, timeout=self.timeout)
        self._check(response, action="Download result")
        content_type = response.headers.get("content-type", "")
        if "json" in content_type:
            return response.json()
        try:
            return response.json()
        except ValueError:
            return response.text
