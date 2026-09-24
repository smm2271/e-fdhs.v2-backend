"""Automatic discovery and registration of FastAPI routers."""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType

from fastapi import APIRouter, FastAPI


def load_routers(app: FastAPI, package_name: str = "routes") -> None:
    """Import every module in *package_name* and register its ``router`` object.

    Route modules may be organised in subpackages. Files beginning with an
    underscore are ignored. A module without a ``router`` attribute is simply
    skipped, which permits shared helpers to live alongside route modules.
    """
    package = importlib.import_module(package_name)
    if not hasattr(package, "__path__"):
        raise ValueError(f"{package_name!r} must be a Python package")

    module_names = sorted(
        module.name
        for module in pkgutil.walk_packages(package.__path__, f"{package.__name__}.")
        if not module.name.rsplit(".", 1)[-1].startswith("_")
    )
    for module_name in module_names:
        _include_router_from_module(app, importlib.import_module(module_name))


def _include_router_from_module(app: FastAPI, module: ModuleType) -> None:
    router = getattr(module, "router", None)
    if router is None:
        return
    if not isinstance(router, APIRouter):
        raise TypeError(
            f"{module.__name__}.router must be a fastapi.APIRouter, "
            f"got {type(router).__name__}"
        )
    app.include_router(router)
