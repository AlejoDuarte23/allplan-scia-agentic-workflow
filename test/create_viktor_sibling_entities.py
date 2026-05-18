from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_API_BASE = "https://demo.viktor.ai/api"
ARTIFACT_PATH = REPO_ROOT / "test" / "artifacts" / "viktor_sibling_entities" / "latest.json"

SOURCES = [
    {
        "workspace_id": 2515,
        "source_entity_id": 11988,
        "label": "workspace-2515",
    },
    {
        "workspace_id": 2544,
        "source_entity_id": 12021,
        "label": "workspace-2544",
    },
]


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


def get_token() -> str:
    load_env_file()
    token = (os.getenv("TOKEN_VK_APP") or os.getenv("VIKTOR_TOKEN") or "").strip()
    if not token:
        raise RuntimeError("Missing TOKEN_VK_APP or VIKTOR_TOKEN in the environment.")
    return token


def check_response(response: requests.Response, action: str) -> None:
    if response.ok:
        return
    raise RuntimeError(f"{action} failed ({response.status_code}): {response.text[:500]}")


def get_json(
    session: requests.Session,
    api_base: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = session.get(f"{api_base.rstrip('/')}/{path.lstrip('/')}", params=params, timeout=(5, 120))
    check_response(response, f"GET {path}")
    return response.json()


def post_json(
    session: requests.Session,
    api_base: str,
    path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    response = session.post(f"{api_base.rstrip('/')}/{path.lstrip('/')}", json=payload, timeout=(5, 120))
    check_response(response, f"POST {path}")
    data = response.json()
    if isinstance(data, list):
        if not data:
            raise RuntimeError("Create entity returned an empty list.")
        return data[0]
    return data


def editor_url(api_base: str, workspace_id: int, entity_id: int) -> str:
    ui_base = api_base.rstrip("/").removesuffix("/api")
    return f"{ui_base}/workspaces/{workspace_id}/app/editor/{entity_id}"


def get_parent_id(session: requests.Session, api_base: str, workspace_id: int, entity_id: int) -> int | None:
    response = session.get(
        f"{api_base.rstrip('/')}/workspaces/{workspace_id}/entities/{entity_id}/parent/",
        timeout=(5, 120),
    )
    if response.status_code in {403, 404}:
        return None
    check_response(response, "GET parent entity")
    return int(response.json()["id"])


def create_sibling(
    session: requests.Session,
    api_base: str,
    *,
    workspace_id: int,
    source_entity_id: int,
    label: str,
) -> dict[str, Any]:
    source = get_json(
        session,
        api_base,
        f"workspaces/{workspace_id}/entities/{source_entity_id}/",
        params={"properties": "true", "clean_params": "true"},
    )
    parent_id = get_parent_id(session, api_base, workspace_id, source_entity_id)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    name = f"Agent sibling smoke {now} - {source['name']}"
    path = (
        f"workspaces/{workspace_id}/entities/{parent_id}/entities/"
        if parent_id is not None
        else f"workspaces/{workspace_id}/entities/"
    )
    created = post_json(
        session,
        api_base,
        path,
        {
            "entity_type": source["entity_type"],
            "name": name,
            "properties": source.get("properties") or {},
        },
    )
    created_id = int(created["id"])
    return {
        "label": label,
        "workspace_id": workspace_id,
        "source_entity_id": source_entity_id,
        "source_name": source["name"],
        "source_editor_url": editor_url(api_base, workspace_id, source_entity_id),
        "created_entity_id": created_id,
        "created_name": created["name"],
        "created_editor_url": editor_url(api_base, workspace_id, created_id),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Create VIKTOR sibling entities and store editor URLs.")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--artifact", type=Path, default=ARTIFACT_PATH)
    args = parser.parse_args()

    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Bearer {get_token()}",
            "Content-Type": "application/json",
        }
    )

    results = [
        create_sibling(
            session,
            args.api_base,
            workspace_id=item["workspace_id"],
            source_entity_id=item["source_entity_id"],
            label=item["label"],
        )
        for item in SOURCES
    ]
    artifact = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "api_base": args.api_base,
        "created_siblings": results,
    }
    args.artifact.parent.mkdir(parents=True, exist_ok=True)
    args.artifact.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(json.dumps(artifact, indent=2))


if __name__ == "__main__":
    main()
