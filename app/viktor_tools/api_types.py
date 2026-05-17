from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class JobCreateRequest(BaseModel):
    """Request payload for creating a job (POST /workspaces/:id/entities/:id/jobs/)."""

    method_name: str = Field(..., min_length=1, description="Name of the method to run")
    params: dict | None = Field(
        default_factory=dict, description="Input parameters for the job"
    )
    poll_result: bool = Field(
        default=False,
        description="Let the endpoint temporarily poll for the result",
    )
    method_type: str | None = Field(default=None, min_length=1)
    editor_session: UUID | None = Field(default=None)
    events: list[str] = Field(default_factory=list)
    timeout: int = Field(default=86400, ge=1, le=86400)


JobStatus = Literal[
    "success",
    "cancelled",
    "failed",
    "running",
    "error",
    "error_user",
    "error_app_reloading",
    "error_timeout",
    "expired",
    "stopped",
    "message",
]


class JobErrorDetail(BaseModel):
    """Error details returned when a job fails."""

    type: str | None = Field(default=None, alias="type")
    message: str | None = None
    invalid_fields: dict | None = None


class JobMessage(BaseModel):
    """Progress message returned while job is running."""

    message_type: Literal["progress"] | str | None = None
    message: str | None = None
    timestamp_epoch: int | None = None
    percentage: int | None = None


class DownloadResult(BaseModel):
    """Download URL wrapper from result payload."""

    url: str


class JobResultPayload(BaseModel):
    """Result payload returned on job success (kind='result')."""

    model_config = {"extra": "allow"}

    web: dict | None = None
    ifc: dict | None = None
    pdf: dict | None = None
    geojson: dict | None = None
    data: Any = None
    image: dict | None = None
    plotly: dict | None = None
    geometry: dict | None = None
    table: dict | None = None
    download: DownloadResult | None = None
    optimization: dict | None = None
    set_params: dict | None = None

    @property
    def download_url(self) -> str | None:
        """Get the download URL if available."""
        return self.download.url if self.download else None


class JobCreateResponse(BaseModel):
    """Response from POST /workspaces/:id/entities/:id/jobs/ (200 or 201)."""

    uid: int | None = None
    url: str | None = Field(
        default=None, description="Poll URL when job is still running (201)"
    )
    message: str | None = None
    kind: str | None = None
    status: JobStatus | None = None
    error_message: str | None = None
    error_stack_trace: dict | None = None
    invalid_fields: dict | None = None
    content: dict | None = None


class JobStatusResponse(BaseModel):
    """Response from GET /jobs/:id/ (polling endpoint)."""

    uid: int
    kind: str = Field(description="'result' or 'result_pointer'")
    status: JobStatus
    completed_at: datetime | None = None
    error: JobErrorDetail | None = None
    result: JobResultPayload | None = None
    message: JobMessage | None = None
    log_download_url: str | None = None

    @model_validator(mode="before")
    @classmethod
    def coerce_result(cls, data: dict) -> dict:
        """Ensure result dict is parsed as JobResultPayload."""
        # Pydantic will handle the conversion automatically
        return data

    def is_success(self) -> bool:
        return self.status == "success"

    def is_running(self) -> bool:
        return self.status == "running"

    def is_failed(self) -> bool:
        return self.status in (
            "failed",
            "cancelled",
            "error",
            "error_user",
            "error_timeout",
        )

    def get_error_message(self) -> str | None:
        if self.error and self.error.message:
            return self.error.message
        return None

    @property
    def download_url(self) -> str | None:
        """Get download URL from result payload."""
        return self.result.download_url if self.result else None
