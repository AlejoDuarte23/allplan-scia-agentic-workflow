__all__ = ["get_viktor_api_tools"]


def __getattr__(name: str):
    if name == "get_viktor_api_tools":
        from .agent_tools import get_viktor_api_tools

        return get_viktor_api_tools
    raise AttributeError(name)
