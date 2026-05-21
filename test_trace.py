from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


API_BASE = "https://demo.viktor.ai/api"
WORKSPACE_ID = 2651
ENTITY_ID = 12137
METHOD_NAME ="view_results"
FINAL_STATUSES = {
    "success",
    "failed",
    "cancelled",
    "error",
    "error_user",
    "error_timeout",
    "expired",
    "stopped",
}


def get_token() -> str:
    load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")
    token = (os.getenv("TOKEN_VK_APP") or os.getenv("VIKTOR_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("Missing TOKEN_VK_APP or VIKTOR_TOKEN.")
    return token


def print_raw(label: str, value: str) -> None:
    print(f"\n--- {label} ---")
    print(value)


def request_json(
    method: str,
    url: str,
    *,
    token: str,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
) -> Any:
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        headers["Content-Type"] = "application/json"

    print_raw("REQUEST", f"{method} {url}")

    response = requests.request(
        method,
        url,
        headers=headers,
        params=params,
        json=payload,
        timeout=(5, 120),
    )
    print_raw("RESPONSE_STATUS", str(response.status_code))
    print_raw("RESPONSE_RAW", response.text)
    response.raise_for_status()
    return response.json()


def main() -> None:
    token = get_token()

    entity_url = f"{API_BASE}/workspaces/{WORKSPACE_ID}/entities/{ENTITY_ID}/"
    entity = request_json(
        "GET",
        entity_url,
        token=token,
        params={
            "properties": "true",
            "clean_params": "true",
            "param_types": "true",
        },
    )
    params = entity.get("properties") or {}

    job_url = f"{API_BASE}/workspaces/{WORKSPACE_ID}/entities/{ENTITY_ID}/jobs/"
    created = request_json(
        "POST",
        job_url,
        token=token,
        payload={
            "method_name": METHOD_NAME,
            "params": params,
            "poll_result": False,
            "timeout": 86400,
        },
    )

    poll_url = created.get("url")
    if not poll_url:
        return

    for attempt in range(1, 31):
        job = request_json("GET", poll_url, token=token)
        status = job.get("status")
        if status in FINAL_STATUSES:
            return
        time.sleep(1)

    raise TimeoutError(f"Job did not finish after 30 poll attempts: {poll_url}")


if __name__ == "__main__":
    main()
