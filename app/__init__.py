__all__ = ["Controller"]


def __getattr__(name: str):
    if name == "Controller":
        from .controller import Controller

        return Controller
    raise AttributeError(name)
