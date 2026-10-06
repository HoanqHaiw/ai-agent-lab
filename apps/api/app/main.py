from contextlib import asynccontextmanager
import asyncio
from pathlib import Path
from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from .workspaces import WorkspaceError, create_workspace_from_github, create_workspace_from_zip, get_workspace, remove_expired_workspaces
from .task_analysis import analyze_task


@asynccontextmanager
async def lifespan(_: FastAPI):
    await remove_expired_workspaces()
    cleanup_task = asyncio.create_task(expire_workspaces_periodically())
    try:
        yield
    finally:
        cleanup_task.cancel()


async def expire_workspaces_periodically() -> None:
    while True:
        await asyncio.sleep(60 * 60)
        await remove_expired_workspaces()


app = FastAPI(
    title="AI Agent Lab API",
    description="Repository-aware task analysis for developers.",
    version="0.1.0",
    lifespan=lifespan,
)

web_origin = os.environ.get("WEB_ORIGIN") or "http://localhost:3000"

app.add_middleware(
    CORSMiddleware,
    allow_origins=[web_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "ai-agent-lab-api"}


@app.post("/api/workspaces/import-zip", status_code=201)
async def import_zip(file: UploadFile = File(...)) -> JSONResponse:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=415, detail="Upload a .zip archive.")
    try:
        result = await create_workspace_from_zip(file)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    finally:
        await file.close()
    return JSONResponse(result)


class GitHubImportRequest(BaseModel):
    repo_url: str


class TaskAnalysisRequest(BaseModel):
    task: str


@app.post("/api/workspaces/{workspace_id}/tasks/analyze")
async def task_analysis(workspace_id: str, request: TaskAnalysisRequest) -> dict:
    task = request.task.strip()
    if len(task) < 8 or len(task) > 4000:
        raise HTTPException(status_code=422, detail="Task description must be between 8 and 4,000 characters.")
    try:
        await get_workspace(workspace_id)
        return await analyze_task(workspace_id, task)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/workspaces/import-github", status_code=201)
async def import_github(request: GitHubImportRequest) -> JSONResponse:
    try:
        result = await create_workspace_from_github(request.repo_url)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return JSONResponse(result)


@app.get("/api/workspaces/{workspace_id}")
async def workspace_details(workspace_id: str) -> dict:
    try:
        workspace = await get_workspace(workspace_id)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return workspace
