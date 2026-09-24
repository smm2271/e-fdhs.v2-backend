import uvicorn

from fastapi import FastAPI

from route_loader import load_routers


def create_app() -> FastAPI:
    app = FastAPI()
    load_routers(app)
    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
