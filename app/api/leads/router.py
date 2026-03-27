from fastapi import APIRouter, Header, HTTPException, Query, status

from app.config import get_settings
from app.infrastructure.db.pool import get_db_pool
from app.infrastructure.db.lead_repo import list_leads

router = APIRouter(prefix="/internal", tags=["internal"])


def _check_key(x_api_key: str | None) -> None:
    key = get_settings().internal_api_key
    if not key:
        return  # key tanımlı değilse auth atla (geliştirme)
    if x_api_key != key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid api key")


@router.get("/leads/{company_id}")
async def get_leads(
    company_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> dict:
    _check_key(x_api_key)
    try:
        pool = get_db_pool()
        leads = await list_leads(pool, company_id=company_id, limit=limit, offset=offset)
        return {"data": leads, "limit": limit, "offset": offset}
    except RuntimeError:
        return {"data": [], "limit": limit, "offset": offset}
