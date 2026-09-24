from fastapi import APIRouter


router = APIRouter()


@router.get("/root")
async def hello() -> dict[str, str]:
    return {"Hello": "World"}
