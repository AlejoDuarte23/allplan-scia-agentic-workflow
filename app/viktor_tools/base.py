import json
import logging
import os
import time
from abc import ABC, abstractmethod
from typing import Any

import requests
from dotenv import load_dotenv

from .api_types import JobCreateResponse, JobResultPayload, JobStatusResponse

load_dotenv()

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

VIKTOR_TOKEN = (os.getenv("TOKEN_VK_APP") or "").strip() or None
MAX_POLL_SECONDS = int(os.getenv("VIKTOR_MAX_POLL_SECONDS", "120"))

API_BASE = os.getenv("VIKTOR_API_BASE", "https://demo.viktor.ai/api").rstrip("/")

HTTP_CONNECT_TIMEOUT = float(os.getenv("VIKTOR_HTTP_CONNECT_TIMEOUT", "5"))
HTTP_READ_TIMEOUT = float(os.getenv("VIKTOR_HTTP_READ_TIMEOUT", "120"))


class ViktorTool(ABC):
    def __init__(
        self,
        workspace_id: int,
        entity_id: int,
        token: str | None = None,
        max_poll_seconds: int | None = None,
        api_base: str = API_BASE,
    ):
        self.workspace_id = workspace_id
        self.entity_id = entity_id
        self.token = (token or VIKTOR_TOKEN or "").strip()
        if not self.token:
            raise ValueError("Missing VIKTOR token (TOKEN_VK_APP).")

        self.max_poll_seconds = max_poll_seconds or MAX_POLL_SECONDS
        self.api_base = api_base.rstrip("/")

        self.job_url = f"{self.api_base}/workspaces/{self.workspace_id}/entities/{self.entity_id}/jobs/"

        self.auth_headers = {"Authorization": f"Bearer {self.token}"}
        self.json_headers = {**self.auth_headers, "Content-Type": "application/json"}

    @abstractmethod
    def build_payload(self) -> dict[str, Any]:
        raise NotImplementedError

    def download_result(self, job: JobStatusResponse) -> dict:
        """Download JSON result from job's download URL."""
        if not job.download_url:
            raise ValueError("No download URL in job result")

        logger.info(f"Downloading result from {job.download_url}")

        response = requests.get(
            job.download_url,
            timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Failed to download (status={response.status_code}): {response.text[:500]}"
            )

        return response.json()

    def poll_job(self, job_url: str) -> JobStatusResponse:
        deadline = time.monotonic() + self.max_poll_seconds
        sleep_s = 0.8

        while time.monotonic() < deadline:
            res = requests.get(
                job_url,
                headers=self.auth_headers,
                timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
            )

            if not res.ok:
                raise RuntimeError(
                    f"Job polling failed (status={res.status_code}): {res.text[:500]}"
                )

            job = JobStatusResponse.model_validate(res.json())

            if job.is_success():
                logger.info("Job completed successfully!")
                return job

            if job.is_failed():
                error_msg = job.get_error_message() or f"status={job.status}"
                raise RuntimeError(f"Job failed: {error_msg}")

            logger.info(f"Job status: {job.status}, polling again...")
            time.sleep(sleep_s)
            sleep_s = min(sleep_s * 1.5, 5.0)

        raise TimeoutError(f"Job did not finish within {self.max_poll_seconds} seconds")

    def run(self) -> JobStatusResponse:
        payload = self.build_payload()

        # Force all tools to run async + we poll ourselves
        payload["poll_result"] = False

        logger.info(f"Submitting job to {self.job_url}")
        logger.info(f"Payload: {json.dumps(payload, indent=2)}")

        response = requests.post(
            url=self.job_url,
            headers=self.json_headers,
            json=payload,
            timeout=(HTTP_CONNECT_TIMEOUT, HTTP_READ_TIMEOUT),
        )

        if not response.ok:
            raise RuntimeError(
                f"Job submission failed (status={response.status_code}): {response.text[:500]}"
            )

        job_create = JobCreateResponse.model_validate(response.json())

        # When job is still running you get a `url` to poll
        if job_create.url:
            logger.info(f"Job created: {job_create.url}")
            return self.poll_job(job_create.url)

        # In case the platform returns a completed job payload synchronously
        if job_create.status == "success":
            logger.info("Job completed synchronously")
            # Convert JobCreateResponse to JobStatusResponse shape
            result_payload = (
                JobResultPayload.model_validate(job_create.content)
                if job_create.content
                else None
            )
            return JobStatusResponse(
                uid=job_create.uid or 0,
                kind=job_create.kind or "result",
                status="success",
                result=result_payload,
            )

        raise RuntimeError(f"Unexpected job response: {job_create.model_dump()}")
