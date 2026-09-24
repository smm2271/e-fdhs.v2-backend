import sys
from pathlib import Path

from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).parents[1]))

from main import app
from route_loader import load_routers


def test_application_discovers_root_router() -> None:
    assert "/root" in app.openapi()["paths"]


def test_loader_includes_router_from_package() -> None:
    target = FastAPI()
    load_routers(target)

    assert "/root" in target.openapi()["paths"]
