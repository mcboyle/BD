from .export_import import export_import_bp

__all__ = ["export_import_bp", "app", "router"]


def __getattr__(name):
    # Lazy: importing the package (e.g. for export_import_bp) must not import
    # fastapi or build the API app as a side effect.
    if name in ("app", "router"):
        from . import api

        return getattr(api, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
