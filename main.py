import uvicorn

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from database.service import (
    ServiceError, AuthorizationError, NotFoundError, ConflictError, ValidationError,
)

from route_loader import load_routers


def create_app() -> FastAPI:
    app = FastAPI()

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, error: ServiceError):
        if isinstance(error, AuthorizationError):
            code = 403
        elif isinstance(error, NotFoundError):
            code = 404
        elif isinstance(error, ConflictError):
            code = 409
        elif isinstance(error, ValidationError):
            code = 422
        else:
            code = 500
        return JSONResponse(status_code=code, content={"detail": str(error)})

    load_routers(app)
    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
