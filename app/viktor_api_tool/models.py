from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AppTarget(BaseModel):
    api_base: str
    workspace_id: int
    entity_id: int
    app_url: str | None = None


class ParamTypeRaw(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str | None = None
    sub_types: dict[str, "ParamTypeRaw"] = Field(default_factory=dict)


class EntityResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    name: str
    entity_type: int
    entity_type_name: str | None = None
    actions: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    param_types: dict[str, ParamTypeRaw] = Field(default_factory=dict)
    parent_count: int = 0
    path: list[int] = Field(default_factory=list)


class ViewRaw(BaseModel):
    model_config = ConfigDict(extra="allow")

    label: str | None = None
    view_type: str | None = None
    controller_method: str | None = None
    automatic_update: bool | None = None


class EntityTypeResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int
    name: str
    class_name: str | None = None
    preprocess_method: str | None = None
    views: list[ViewRaw] = Field(default_factory=list)


class EditorSessionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    editor_session: UUID


class ParametrizationViewRaw(BaseModel):
    model_config = ConfigDict(extra="allow")

    controller_method: str | None = None


class ParamNodeRaw(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    type: str
    title: str | None = None
    ui_name: str | None = None
    description: str | None = None
    default: Any = None
    views: list[str] = Field(default_factory=list)
    content: list["ParamNodeRaw"] = Field(default_factory=list)
    method: str | None = None
    options: Any = None
    variant: str | None = None
    suffix: str | None = None
    multiple: bool | None = None


class ParametrizationContentRaw(BaseModel):
    model_config = ConfigDict(extra="allow")

    parametrization: list[ParamNodeRaw] = Field(default_factory=list)
    views: list[ParametrizationViewRaw] = Field(default_factory=list)


class ParametrizationResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    content: ParametrizationContentRaw


class MethodSpec(BaseModel):
    kind: Literal["view", "button", "preprocess"]
    method_name: str
    source: Literal["entity_type", "parametrization"]
    label: str | None = None
    view_type: str | None = None
    automatic_update: bool | None = None
    source_path: list[str] = Field(default_factory=list)
    raw_node_type: str | None = None
    method_classification: str | None = None
    expected_result_kind: str | None = None


class ParamNodeSpec(BaseModel):
    node_kind: Literal[
        "page",
        "step",
        "tab",
        "section",
        "field",
        "table",
        "dynamic_array",
        "action",
        "unknown",
    ]
    raw_type: str
    name: str | None = None
    title: str | None = None
    label: str | None = None
    path: list[str] = Field(default_factory=list)
    payload_path: list[str] = Field(default_factory=list)
    views: list[str] = Field(default_factory=list)
    method: str | None = None
    default: Any = None
    python_type_hint: str | None = None
    metadata: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)
    children: list["ParamNodeSpec"] = Field(default_factory=list)


class PayloadNodeSpec(BaseModel):
    value_kind: Literal["object", "list", "scalar", "null"]
    path: list[str] = Field(default_factory=list)
    python_type_hint: str
    example: Any = None
    children: list["PayloadNodeSpec"] = Field(default_factory=list)
    item: "PayloadNodeSpec | None" = None


class CaptureSummary(BaseModel):
    total_nodes: int
    total_containers: int
    total_fields: int
    total_actions: int
    total_methods: int


class PayloadCandidate(BaseModel):
    name: str
    params: dict[str, Any] = Field(default_factory=dict)
    payload_tree: PayloadNodeSpec | None = None
    explicit_default_paths: list[str] = Field(default_factory=list)
    missing_default_paths: list[str] = Field(default_factory=list)


class ParametrizationCapture(BaseModel):
    api_base: str
    workspace_id: int
    entity_id: int
    editor_session: UUID
    entity: EntityResponse
    entity_type: EntityTypeResponse
    parametrization: ParametrizationResponse
    methods: list[MethodSpec] = Field(default_factory=list)
    parametrization_tree: list[ParamNodeSpec] = Field(default_factory=list)
    saved_payload_tree: PayloadNodeSpec | None = None
    default_payload: PayloadCandidate
    default_plus_saved_payload: PayloadCandidate
    data_methods: list[MethodSpec] = Field(default_factory=list)
    summary: CaptureSummary


class DownloadResult(BaseModel):
    url: str


class JobResultPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    web: dict[str, Any] | None = None
    ifc: dict[str, Any] | None = None
    pdf: dict[str, Any] | None = None
    geojson: dict[str, Any] | None = None
    data: Any = None
    image: dict[str, Any] | None = None
    plotly: dict[str, Any] | None = None
    geometry: dict[str, Any] | None = None
    table: dict[str, Any] | None = None
    download: DownloadResult | None = None
    optimization: dict[str, Any] | None = None
    set_params: dict[str, Any] | None = None

    @property
    def download_url(self) -> str | None:
        return self.download.url if self.download else None


class JobCreateResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    uid: int | None = None
    url: str | None = None
    message: str | None = None
    kind: str | None = None
    status: str | None = None
    error_message: str | None = None
    content: dict[str, Any] | None = None


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(extra="allow")

    uid: int | None = None
    kind: str | None = None
    status: str
    completed_at: datetime | None = None
    result: JobResultPayload | None = None
    error: dict[str, Any] | None = None
    message: dict[str, Any] | None = None
    log_download_url: str | None = None

    @property
    def download_url(self) -> str | None:
        return self.result.download_url if self.result else None

    def is_success(self) -> bool:
        return self.status == "success"

    def is_failed(self) -> bool:
        return self.status in {
            "failed",
            "cancelled",
            "error",
            "error_user",
            "error_timeout",
            "expired",
            "stopped",
        }
