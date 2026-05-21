from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from .client import ViktorApiClient
from .models import (
    AppTarget,
    CaptureSummary,
    EntityTypeResponse,
    JobResultPayload,
    MethodSpec,
    ParamNodeRaw,
    ParamNodeSpec,
    ParamTypeRaw,
    ParametrizationCapture,
    ParametrizationResponse,
    PayloadCandidate,
    PayloadNodeSpec,
)

FIELD_TYPE_MAP = {
    "text": "str",
    "textarea": "str",
    "number": "float",
    "integer": "int",
    "toggle": "bool",
    "boolean": "bool",
    "date": "str",
    "autocomplete": "str",
    "select": "str",
    "optionfield": "str",
    "color": "dict[str, int]",
}
CONTAINER_NODE_KINDS = {"page", "step", "tab", "section"}
_MISSING = object()


def split_param_path(name: str | None) -> list[str]:
    if not name:
        return []
    return [part for part in name.split(".") if part]


def classify_node_kind(raw_type: str) -> str:
    lowered = raw_type.strip().lower()
    if lowered in CONTAINER_NODE_KINDS:
        return lowered
    if lowered == "dynamicarray":
        return "dynamic_array"
    if lowered == "table":
        return "table"
    if "button" in lowered:
        return "action"
    if lowered:
        return "field"
    return "unknown"


def classify_method_spec(
    *,
    kind: str,
    view_type: str | None,
    raw_node_type: str | None,
) -> tuple[str, str | None]:
    lowered_view_type = (view_type or "").strip().lower()
    lowered_node_type = (raw_node_type or "").strip().lower()

    if kind == "view":
        if lowered_view_type == "web":
            return ("webview", "web")
        if lowered_view_type == "data":
            return ("dataview", "data")
        if lowered_view_type == "table":
            return ("tableview", "table")
        if lowered_view_type == "plotly":
            return ("plotlyview", "plotly")
        if lowered_view_type == "pdf":
            return ("pdfview", "pdf")
        if lowered_view_type:
            return (f"{lowered_view_type}_view", lowered_view_type)
        return ("view", None)

    if kind == "button":
        if lowered_node_type == "download-button":
            return ("download_button", "download")
        return ("button", None)

    if kind == "preprocess":
        return ("preprocess", None)

    return (kind, None)


def extract_option_values(options: Any) -> list[str]:
    if options is None:
        return []
    if isinstance(options, list):
        values: list[str] = []
        for item in options:
            if isinstance(item, dict):
                label = item.get("label") or item.get("value") or item.get("name")
                if label is not None:
                    values.append(str(label))
            else:
                values.append(str(item))
        return values
    if isinstance(options, dict):
        if isinstance(options.get("options"), list):
            return extract_option_values(options["options"])
        return [f"{key}={value}" for key, value in sorted(options.items())]
    return [str(options)]


def infer_python_type_hint(
    node: ParamNodeRaw,
    param_type: ParamTypeRaw | None,
) -> str | None:
    node_kind = classify_node_kind(node.type)
    base_type = (
        (param_type.type or node.type).strip().lower()
        if param_type
        else node.type.strip().lower()
    )

    if node_kind in {"dynamic_array", "table"}:
        return "list[dict[str, Any]]"
    if node_kind == "action":
        return None
    if node_kind in CONTAINER_NODE_KINDS:
        return "dict[str, Any]"
    if node.multiple or isinstance(node.default, list):
        if base_type in {"select", "optionfield", "text"}:
            return "list[str]"
        return "list[Any]"
    return FIELD_TYPE_MAP.get(base_type, "Any")


def build_metadata(node: ParamNodeRaw, param_type: ParamTypeRaw | None) -> list[str]:
    metadata: list[str] = []
    if node.ui_name:
        metadata.append(f"label: {node.ui_name}")
    if node.title:
        metadata.append(f"title: {node.title}")
    if node.description:
        metadata.append(f"description: {node.description}")
    if node.default is not None:
        metadata.append(f"default: {node.default!r}")
    if node.suffix:
        metadata.append(f"suffix: {node.suffix}")
    if node.variant:
        metadata.append(f"variant: {node.variant}")
    if node.views:
        metadata.append(f"views: {', '.join(node.views)}")
    if node.method:
        metadata.append(f"method: {node.method}")

    option_values = extract_option_values(node.options)
    if option_values:
        metadata.append(f"options: {', '.join(option_values)}")
    if param_type and param_type.type:
        metadata.append(f"param_type: {param_type.type}")
    if param_type and param_type.sub_types:
        metadata.append(f"sub_types: {', '.join(sorted(param_type.sub_types))}")
    return metadata


def extract_node_extra(node: ParamNodeRaw) -> dict[str, Any]:
    known_fields = set(ParamNodeRaw.model_fields)
    payload = node.model_dump(mode="json")
    return {
        key: value
        for key, value in payload.items()
        if key not in known_fields and value not in (None, "", [], {})
    }


def normalize_param_node(
    node: ParamNodeRaw,
    param_types: dict[str, ParamTypeRaw],
) -> ParamNodeSpec:
    payload_path = split_param_path(node.name)
    param_type = param_types.get(node.name or "")
    return ParamNodeSpec(
        node_kind=classify_node_kind(node.type),
        raw_type=node.type,
        name=node.name,
        title=node.title,
        label=node.ui_name,
        path=payload_path,
        payload_path=payload_path,
        views=list(node.views),
        method=node.method,
        default=node.default,
        python_type_hint=infer_python_type_hint(node, param_type),
        metadata=build_metadata(node, param_type),
        extra=extract_node_extra(node),
        children=[normalize_param_node(child, param_types) for child in node.content],
    )


def iter_param_nodes(nodes: Iterable[ParamNodeRaw]) -> Iterable[ParamNodeRaw]:
    for node in nodes:
        yield node
        yield from iter_param_nodes(node.content)


def extract_methods(
    entity_type: EntityTypeResponse,
    parametrization: ParametrizationResponse,
) -> list[MethodSpec]:
    methods: dict[tuple[str, str], MethodSpec] = {}

    if entity_type.preprocess_method:
        key = ("preprocess", entity_type.preprocess_method)
        methods[key] = MethodSpec(
            kind="preprocess",
            method_name=entity_type.preprocess_method,
            source="entity_type",
        )

    for view in entity_type.views:
        if not view.controller_method:
            continue
        method_classification, expected_result_kind = classify_method_spec(
            kind="view",
            view_type=view.view_type,
            raw_node_type=None,
        )
        key = ("view", view.controller_method)
        methods[key] = MethodSpec(
            kind="view",
            method_name=view.controller_method,
            source="entity_type",
            label=view.label,
            view_type=view.view_type,
            automatic_update=view.automatic_update,
            method_classification=method_classification,
            expected_result_kind=expected_result_kind,
        )

    for node in iter_param_nodes(parametrization.content.parametrization):
        if not node.method:
            continue
        method_classification, expected_result_kind = classify_method_spec(
            kind="button",
            view_type=None,
            raw_node_type=node.type,
        )
        key = ("button", node.method)
        methods[key] = MethodSpec(
            kind="button",
            method_name=node.method,
            source="parametrization",
            label=node.ui_name or node.title,
            source_path=split_param_path(node.name),
            raw_node_type=node.type,
            method_classification=method_classification,
            expected_result_kind=expected_result_kind,
        )

    return list(methods.values())


def deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        merged = deepcopy(base)
        for key, value in override.items():
            merged[key] = deep_merge(merged[key], value) if key in merged else deepcopy(value)
        return merged
    return deepcopy(override)


def build_nested_override(path: list[str], value: Any) -> dict[str, Any]:
    nested: Any = deepcopy(value)
    for key in reversed(path):
        nested = {key: nested}
    return nested


def node_has_explicit_default(node: ParamNodeRaw) -> bool:
    return "default" in node.model_fields_set


def extract_default_payload_from_node(node: ParamNodeRaw) -> Any:
    node_kind = classify_node_kind(node.type)
    child_payload = extract_default_payload_from_nodes(node.content)

    if node_kind in CONTAINER_NODE_KINDS:
        return child_payload if child_payload else _MISSING
    if node_kind == "action":
        return _MISSING
    if node.name and node_has_explicit_default(node):
        return build_nested_override(split_param_path(node.name), deepcopy(node.default))
    if child_payload:
        return child_payload
    return _MISSING


def extract_default_payload_from_nodes(nodes: Iterable[ParamNodeRaw]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for node in nodes:
        node_payload = extract_default_payload_from_node(node)
        if node_payload is _MISSING:
            continue
        merged = deep_merge(merged, node_payload)
    return merged


def collect_leaf_field_paths(nodes: Iterable[ParamNodeRaw]) -> list[str]:
    paths: list[str] = []
    for node in nodes:
        if classify_node_kind(node.type) in {"field", "table", "dynamic_array"} and node.name:
            paths.append(node.name)
        paths.extend(collect_leaf_field_paths(node.content))
    return paths


def collect_explicit_default_paths(nodes: Iterable[ParamNodeRaw]) -> list[str]:
    paths: list[str] = []
    for node in nodes:
        if (
            node.name
            and classify_node_kind(node.type) in {"field", "table", "dynamic_array"}
            and node_has_explicit_default(node)
        ):
            paths.append(node.name)
        paths.extend(collect_explicit_default_paths(node.content))
    return paths


def path_exists_in_payload(payload: Any, path: list[str]) -> bool:
    current = payload
    for part in path:
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def merge_missing_with_fallback(primary: Any, fallback: Any) -> Any:
    if isinstance(primary, dict) and isinstance(fallback, dict):
        merged = deepcopy(primary)
        for key, fallback_value in fallback.items():
            if key not in merged:
                merged[key] = deepcopy(fallback_value)
            else:
                merged[key] = merge_missing_with_fallback(merged[key], fallback_value)
        return merged
    return deepcopy(primary)


def merge_values(values: list[Any]) -> Any:
    filtered = [value for value in values if value is not None]
    if not filtered:
        return None
    if all(isinstance(value, dict) for value in filtered):
        merged: dict[str, Any] = {}
        for key in sorted({key for value in filtered for key in value}):
            merged[key] = merge_values([value.get(key) for value in filtered])
        return merged
    if all(isinstance(value, list) for value in filtered):
        flattened: list[Any] = []
        for value in filtered:
            flattened.extend(value)
        return [merge_values(flattened)] if flattened else []
    return filtered[0]


def infer_scalar_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return "Any"


def build_payload_tree(value: Any, path: list[str] | None = None) -> PayloadNodeSpec:
    current_path = path or []
    if value is None:
        return PayloadNodeSpec(value_kind="null", path=current_path, python_type_hint="None")
    if isinstance(value, dict):
        return PayloadNodeSpec(
            value_kind="object",
            path=current_path,
            python_type_hint="dict[str, Any]",
            children=[
                build_payload_tree(child_value, current_path + [child_key])
                for child_key, child_value in sorted(value.items())
            ],
        )
    if isinstance(value, list):
        if not value:
            return PayloadNodeSpec(
                value_kind="list",
                path=current_path,
                python_type_hint="list[Any]",
                example=[],
            )
        item_node = build_payload_tree(merge_values(value), current_path + ["[]"])
        if item_node.value_kind == "scalar":
            python_type_hint = f"list[{item_node.python_type_hint}]"
        elif item_node.value_kind == "null":
            python_type_hint = "list[Any]"
        else:
            python_type_hint = "list[dict[str, Any]]"
        return PayloadNodeSpec(
            value_kind="list",
            path=current_path,
            python_type_hint=python_type_hint,
            example=value[:2],
            item=item_node,
        )
    return PayloadNodeSpec(
        value_kind="scalar",
        path=current_path,
        python_type_hint=infer_scalar_type(value),
        example=value,
    )


def build_default_payload_candidate(
    parametrization: ParametrizationResponse,
    saved_params: dict[str, Any],
    *,
    name: str = "defaults_only",
    backfill_missing: bool = False,
) -> PayloadCandidate:
    nodes = parametrization.content.parametrization
    explicit_default_paths = sorted(set(collect_explicit_default_paths(nodes)))
    all_leaf_paths = sorted(set(collect_leaf_field_paths(nodes)))
    default_params = extract_default_payload_from_nodes(nodes)
    missing_default_paths = [
        field_path
        for field_path in all_leaf_paths
        if not path_exists_in_payload(default_params, split_param_path(field_path))
    ]
    if backfill_missing:
        # Defaults initialize the payload shape, but saved entity values are the
        # current state and must win when both exist.
        params = deep_merge(default_params, saved_params)
        name = "defaults_plus_saved"
    else:
        params = default_params
    return PayloadCandidate(
        name=name,
        params=params,
        payload_tree=build_payload_tree(params, []),
        explicit_default_paths=explicit_default_paths,
        missing_default_paths=missing_default_paths,
    )


def build_summary(
    parametrization_tree: list[ParamNodeSpec],
    methods: list[MethodSpec],
) -> CaptureSummary:
    total_nodes = 0
    total_containers = 0
    total_fields = 0
    total_actions = 0

    def walk(node: ParamNodeSpec) -> None:
        nonlocal total_nodes, total_containers, total_fields, total_actions
        total_nodes += 1
        if node.node_kind in CONTAINER_NODE_KINDS:
            total_containers += 1
        elif node.node_kind in {"field", "table", "dynamic_array"}:
            total_fields += 1
        elif node.node_kind == "action":
            total_actions += 1
        for child in node.children:
            walk(child)

    for top_level_node in parametrization_tree:
        walk(top_level_node)

    return CaptureSummary(
        total_nodes=total_nodes,
        total_containers=total_containers,
        total_fields=total_fields,
        total_actions=total_actions,
        total_methods=len(methods),
    )


def detect_result_kind(result: JobResultPayload | None) -> tuple[str | None, list[str], dict[str, Any]]:
    if result is None:
        return (None, [], {})
    payload = result.model_dump(mode="json", exclude_none=True)
    result_keys = sorted(payload.keys())
    result_kind = result_keys[0] if result_keys else None
    summary: dict[str, Any] = {}
    if result_kind == "download":
        summary["url"] = payload.get("download", {}).get("url")
    elif result_kind == "web":
        summary["url"] = payload.get("web", {}).get("url")
    elif result_kind in {"plotly", "data", "table", "pdf", "image", "geometry"}:
        value = payload.get(result_kind)
        if isinstance(value, dict):
            summary["keys"] = sorted(value.keys())[:20]
    return (result_kind, result_keys, summary)


def capture_app(
    *,
    client: ViktorApiClient,
    target: AppTarget,
    params: dict[str, Any] | None = None,
) -> ParametrizationCapture:
    entity = client.get_entity(target)
    entity_type = client.get_entity_type(
        workspace_id=target.workspace_id,
        entity_type_id=entity.entity_type,
    )
    editor_session = client.create_editor_session(target)
    effective_params = deepcopy(entity.properties if params is None else params)
    parametrization = client.get_parametrization(
        target,
        editor_session=str(editor_session.editor_session),
        params=effective_params,
    )
    parametrization_tree = [
        normalize_param_node(node, entity.param_types)
        for node in parametrization.content.parametrization
    ]
    methods = extract_methods(entity_type, parametrization)
    data_methods = [
        method
        for method in methods
        if method.expected_result_kind in {"data", "table"}
        or method.view_type in {"data", "table"}
    ]
    default_payload = build_default_payload_candidate(
        parametrization,
        entity.properties,
        name="defaults_only",
    )
    default_plus_saved = build_default_payload_candidate(
        parametrization,
        entity.properties,
        backfill_missing=True,
    )
    summary = build_summary(parametrization_tree, methods)
    return ParametrizationCapture(
        api_base=target.api_base,
        workspace_id=target.workspace_id,
        entity_id=target.entity_id,
        editor_session=editor_session.editor_session,
        entity=entity,
        entity_type=entity_type,
        parametrization=parametrization,
        methods=methods,
        data_methods=data_methods,
        parametrization_tree=parametrization_tree,
        saved_payload_tree=build_payload_tree(entity.properties, []),
        default_payload=default_payload,
        default_plus_saved_payload=default_plus_saved,
        summary=summary,
    )


def compact_capture(capture: ParametrizationCapture, *, include_raw: bool = False) -> dict[str, Any]:
    payload = capture.model_dump(mode="json")
    if include_raw:
        return payload
    return {
        "api_base": payload["api_base"],
        "workspace_id": payload["workspace_id"],
        "entity_id": payload["entity_id"],
        "entity": {
            "id": payload["entity"]["id"],
            "name": payload["entity"]["name"],
            "entity_type": payload["entity"]["entity_type"],
            "entity_type_name": payload["entity"].get("entity_type_name"),
            "actions": payload["entity"].get("actions", []),
            "properties": payload["entity"].get("properties", {}),
        },
        "entity_type": {
            "id": payload["entity_type"]["id"],
            "name": payload["entity_type"]["name"],
            "class_name": payload["entity_type"].get("class_name"),
            "preprocess_method": payload["entity_type"].get("preprocess_method"),
        },
        "summary": payload["summary"],
        "methods": payload["methods"],
        "data_methods": payload["data_methods"],
        "parametrization_tree": payload["parametrization_tree"],
        "saved_payload_tree": payload["saved_payload_tree"],
        "default_payload": payload["default_payload"],
        "default_plus_saved_payload": payload["default_plus_saved_payload"],
    }
