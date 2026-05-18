from typing import Any


def get_agent_tools() -> list[Any]:
    from app.tools import get_tools

    return get_tools()


def get_tool_display_names() -> dict[str, str]:
    from app.tools import TOOL_DISPLAY_NAMES

    return TOOL_DISPLAY_NAMES
