# AI Agent Lab

AI Software Engineer Agent demo focused on helping developers understand a handed-off task in the context of a repository.

## MVP goal

Given a repository and a task description, the app should identify relevant code, explain the task in repository context, surface open questions, and produce a reviewable implementation plan with file-level evidence.

The product supports two repository inputs:

- GitHub repository: clone it into an isolated workspace.
- Local project: upload it as a ZIP and extract it into an isolated workspace.

Both inputs use the same analysis flow after import. The API can ask Gemini to summarize a task, return open questions and a plan, and cite relevant repository files and line ranges. The app does not execute repository code or make code changes.

## Proposed stack

- Web UI: Next.js, TypeScript, Tailwind CSS.
- API and agent orchestration: Python, FastAPI.
- App data: MongoDB Atlas Free for project metadata, analysis runs, plans, and chat history.
- LLM: Gemini API, called only by the backend through a provider abstraction.
- Repository content: temporary workspace storage, separate from MongoDB.

## Product flow

See [MVP flow and roadmap](docs/MVP-FLOW.md).

## Configuration principles

- Keep `GEMINI_API_KEY`, `MONGODB_URI`, and any GitHub credentials on the backend.
- Never commit secrets or expose provider keys in browser code.
- Treat imported repositories and their contents as untrusted input.
- Do not run commands or repository code in the analysis-only MVP.
- Free-tier provider limits and data policies apply; avoid importing confidential repositories into a demo deployment.

## Current status

The frontend has working GitHub public repository and ZIP imports connected to FastAPI. Both sources create temporary workspaces and display repository files plus deterministic language/framework signals from source extensions and manifests. Task analysis uses Gemini through the backend and returns a summary, assumptions, questions, a plan, verification suggestions, and validated file citations. MongoDB Atlas metadata persistence is enabled when `MONGODB_URI` is configured; without it, local development stores metadata in the workspace directory.

## Run locally

Web app:

```powershell
cd apps/web
pnpm install
pnpm dev
```

API (Python 3.11+):

```powershell
cd apps/api
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Copy `apps/api/.env.example` to `apps/api/.env`, then set `GEMINI_API_KEY` to a key from Google AI Studio. The key stays on the backend. `GEMINI_MODEL` defaults to `gemini-3.8-flash`. Set `MONGODB_URI` to use Atlas metadata storage; without it, local development stores metadata in the workspace directory. Workspace file contents are stored under `apps/api/.workspaces` and expire after seven days. Configure `NEXT_PUBLIC_API_BASE_URL` in `apps/web/.env.local` if the API is not running on `http://localhost:8000`.

The API host needs Git installed for public GitHub imports. The API health check is available at `http://localhost:8000/health`. ZIP import uses `POST /api/workspaces/import-zip`, public GitHub import uses `POST /api/workspaces/import-github`, workspace details use `GET /api/workspaces/{workspace_id}`, and task analysis uses `POST /api/workspaces/{workspace_id}/tasks/analyze` with `{"task":"..."}`. Analysis sends bounded text snippets to Gemini; do not upload confidential repositories when using a free-tier key.
