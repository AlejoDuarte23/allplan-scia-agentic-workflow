from __future__ import annotations

import json
from pathlib import Path

from dotenv import load_dotenv

from app.viktor_api_tool.client import AppTarget, ViktorApiClient
from app.viktor_api_tool.discovery import detect_result_kind


# Test script for SCIA app view_results method.
WORKSPACE_ID = 2651
ENTITY_ID = 12137
METHOD_NAME = "view_results"
API_BASE = "https://demo.viktor.ai/api"


def main() -> None:
    load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

    target = AppTarget(
        api_base=API_BASE,
        workspace_id=WORKSPACE_ID,
        entity_id=ENTITY_ID,
    )
    client = ViktorApiClient(api_base=API_BASE)

    entity = client.get_entity(target)
    print(f"Entity: id={entity.id}, name={entity.name}")

    params = entity.properties
    print("Running method with saved params...")

    job = client.create_job(
        target,
        method_name=METHOD_NAME,
        params=params,
        timeout=86400,
    )
    result_kind, result_keys, result_summary = detect_result_kind(job.result)
    response = {
        "status": job.status,
        "kind": job.kind,
        "uid": job.uid,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "actual_result_kind": result_kind,
        "result_keys": result_keys,
        "result_summary": result_summary,
        "error": job.error,
        "message": job.message,
        "log_download_url": job.log_download_url,
        "result": job.result.model_dump(mode="json") if job.result else None,
    }
    print("Job finished. Raw result:")
    print(json.dumps(response, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
