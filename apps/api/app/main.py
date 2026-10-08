from contextlib import asynccontextmanager
import asyncio
from pathlib import Path
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import os
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

from .workspaces import WorkspaceError, approve_workspace_analysis, create_workspace_from_github, create_workspace_from_zip, ensure_workspace_owner, get_workspace, read_workspace_file, remove_expired_workspaces, save_workspace_analysis, save_workspace_chat_turn, save_workspace_code_review
from .task_analysis import analyze_task
from .developer_chat import answer_project_question
from .code_intelligence import review_workspace
from .auth import get_current_user_id
from .implementation_engine import apply_implementation_draft, create_implementation_draft, get_implementation_draft, rollback_implementation_draft
from .verification import run_verification_check, verification_options


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


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "service": "AI Agent Lab API",
        "status": "ok",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "ai-agent-lab-api"}


@app.post("/api/workspaces/import-zip", status_code=201)
async def import_zip(file: UploadFile = File(...), user_id: str | None = Depends(get_current_user_id)) -> JSONResponse:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=415, detail="Upload a .zip archive.")
    try:
        result = await create_workspace_from_zip(file, owner_id=user_id)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    finally:
        await file.close()
    return JSONResponse(result)


class GitHubImportRequest(BaseModel):
    repo_url: str


class TaskAnalysisRequest(BaseModel):
    task: str


class ProjectChatRequest(BaseModel):
    message: str


@app.get("/api/workspaces/{workspace_id}/files")
async def workspace_file(workspace_id: str, path: str, user_id: str | None = Depends(get_current_user_id)) -> dict:
    try:
        ensure_workspace_owner(await get_workspace(workspace_id), user_id)
        return await read_workspace_file(workspace_id, path)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/workspaces/{workspace_id}/tasks/analyze")
async def task_analysis(workspace_id: str, request: TaskAnalysisRequest, user_id: str | None = Depends(get_current_user_id)) -> dict:
    task = request.task.strip()
    if len(task) < 8 or len(task) > 4000:
        raise HTTPException(status_code=422, detail="Task description must be between 8 and 4,000 characters.")
    try:
        ensure_workspace_owner(await get_workspace(workspace_id), user_id)
        result = await analyze_task(workspace_id, task)
        record = await save_workspace_analysis(workspace_id, task, result)
        return {**record}
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/workspaces/{workspace_id}/tasks/{analysis_id}/approve")
async def approve_task_plan(workspace_id: str, analysis_id: str, user_id: str | None = Depends(get_current_user_id)) -> dict:
    try:
        ensure_workspace_owner(await get_workspace(workspace_id), user_id)
        return await approve_workspace_analysis(workspace_id, analysis_id)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/workspaces/{workspace_id}/tasks/{analysis_id}/implementation-draft")
async def implementation_draft(workspace_id: str, analysis_id: str, user_id: str | None = Depends(get_current_user_id)) -> dict:
    try:
        ensure_workspace_owner(await get_workspace(workspace_id), user_id)
        return await create_implementation_draft(workspace_id, analysis_id)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.get("/api/workspaces/{workspace_id}/tasks/implementation-drafts/{draft_id}")
async def read_implementation_draft(workspace_id: str, draft_id: str, user_id: str | None = Depends(get_current_user_id)) -> dict:
    try:
        ensure_workspace_owner(await get_workspace(workspace_id), user_id)
        return await get_implementation_draft(workspace_id, draft_id)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/workspaces/{workspace_id}/tasks/implementation-drafts/{draft_id}/apply")
async def apply_workspace_implementation(workspace_id: str, draft_id: str, user_id: str | None = Depends(get_current_user_id)) -> dict:
    try:
        ensure_workspace_owner(await get_workspace(workspace_id), user_id)
        return await apply_implementation_draft(workspace_id, draft_id)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/workspaces/{workspace_id}/tasks/implementation-drafts/{draft_id}/rollback")
async def rollback_workspace_implementation(workspace_id: str, draft_id: str, user_id: str | None = Depends(get_current_user_id)) -> dict:
    try:
        ensure_workspace_owner(await get_workspace(workspace_id), user_id)
        return await rollback_implementation_draft(workspace_id, draft_id)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/workspaces/{workspace_id}/chat")
async def project_chat(workspace_id: str, request: ProjectChatRequest, user_id: str | None = Depends(get_current_user_id)) -> dict:
    question = request.message.strip()
    if len(question) < 2 or len(question) > 2000:
        raise HTTPException(status_code=422, detail="Chat message must be between 2 and 2,000 characters.")
    try:
        workspace = await get_workspace(workspace_id)
        ensure_workspace_owner(workspace, user_id)
        result = await answer_project_question(workspace_id, question, workspace.get("chat_messages", []))
        return await save_workspace_chat_turn(workspace_id, question, result)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/workspaces/{workspace_id}/code-intelligence/review")
async def code_intelligence_review(workspace_id: str, user_id: str | None = Depends(get_current_user_id)) -> dict:
    try:
        ensure_workspace_owner(await get_workspace(workspace_id), user_id)
        result = await review_workspace(workspace_id)
        return await save_workspace_code_review(workspace_id, result)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.get("/api/workspaces/{workspace_id}/verification")
async def workspace_verification_options(workspace_id: str, user_id: str | None = Depends(get_current_user_id)) -> dict:
    try:
        ensure_workspace_owner(await get_workspace(workspace_id), user_id)
        return await verification_options(workspace_id)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


class VerificationRequest(BaseModel):
    check_id: str


@app.post("/api/workspaces/{workspace_id}/verification")
async def run_workspace_verification(workspace_id: str, request: VerificationRequest, user_id: str | None = Depends(get_current_user_id)) -> dict:
    try:
        ensure_workspace_owner(await get_workspace(workspace_id), user_id)
        return await run_verification_check(workspace_id, request.check_id)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/workspaces/import-github", status_code=201)
async def import_github(request: GitHubImportRequest, user_id: str | None = Depends(get_current_user_id)) -> JSONResponse:
    try:
        result = await create_workspace_from_github(request.repo_url, owner_id=user_id)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return JSONResponse(result)


@app.get("/api/workspaces/{workspace_id}")
async def workspace_details(workspace_id: str, user_id: str | None = Depends(get_current_user_id)) -> dict:
    try:
        workspace = await get_workspace(workspace_id)
        ensure_workspace_owner(workspace, user_id)
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    return workspace
