from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from typing import Optional

from ..database import get_settings, save_settings, get_logs, get_metrics, get_mirror_stats
from ..services import nethunt, helpcrunch
from .. import sync_engine
from ..models.schemas import SettingsUpdate, TestConnectionRequest, FolderFieldsRequest
from .auth import get_current_user

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/settings")
async def get_settings_endpoint(username: str = Depends(get_current_user)):
    return get_settings()


@router.post("/settings")
async def save_settings_endpoint(payload: SettingsUpdate, username: str = Depends(get_current_user)):
    try:
        updated = save_settings(payload.dict())
        return {"status": "success", "settings": updated}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/logs")
async def get_logs_endpoint(limit: int = 100, status: Optional[str] = None, username: str = Depends(get_current_user)):
    return get_logs(limit=limit, status_filter=status)


@router.get("/metrics")
async def get_metrics_endpoint(username: str = Depends(get_current_user)):
    return get_metrics()


@router.post("/test-nethunt")
async def test_nethunt(payload: TestConnectionRequest, username: str = Depends(get_current_user)):
    success = await nethunt.test_connection(payload.email, payload.key, payload.base_url)
    if success:
        return {"status": "success", "message": "Successfully connected to NetHunt CRM"}
    raise HTTPException(status_code=400, detail="Failed to connect to NetHunt CRM. Please check your credentials.")


@router.post("/test-helpcrunch")
async def test_helpcrunch(payload: TestConnectionRequest, username: str = Depends(get_current_user)):
    success = await helpcrunch.test_connection(payload.key)
    if success:
        return {"status": "success", "message": "Successfully connected to HelpCrunch API"}
    raise HTTPException(status_code=400, detail="Failed to connect to HelpCrunch. Please check your credentials.")


@router.post("/nethunt/folders")
async def nethunt_folders(payload: TestConnectionRequest, username: str = Depends(get_current_user)):
    folders = await nethunt.list_folders(payload.email, payload.key, payload.base_url)
    return folders


@router.post("/nethunt/folder-fields")
async def nethunt_folder_fields(payload: FolderFieldsRequest, username: str = Depends(get_current_user)):
    fields = await nethunt.list_folder_fields(payload.email, payload.key, payload.base_url, payload.folder_id)
    return fields


@router.post("/sync/full")
async def sync_full(background_tasks: BackgroundTasks, username: str = Depends(get_current_user)):
    background_tasks.add_task(sync_engine.run_full_sync)
    return {"status": "queued", "message": "Full sync started in the background."}


@router.get("/sync/stats")
async def sync_stats(username: str = Depends(get_current_user)):
    return get_mirror_stats()
