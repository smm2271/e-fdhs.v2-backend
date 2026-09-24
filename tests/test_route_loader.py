import sys
from pathlib import Path

from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).parents[1]))

from main import app
from route_loader import load_routers


def test_application_discovers_root_router() -> None:
    paths = app.openapi()["paths"]
    assert {"/root", "/auth/login", "/auth/logout", "/auth/me", "/users/me", "/users/me/password"}.issubset(paths)


def test_loader_includes_router_from_package() -> None:
    target = FastAPI()
    load_routers(target)

    paths = target.openapi()["paths"]
    assert {"/root", "/auth/login", "/auth/logout", "/auth/me", "/users/me", "/users/me/password"}.issubset(paths)
