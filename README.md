# AI Agent Lab

AI Software Engineer Agent demo focused on helping developers understand a handed-off task in the context of a repository.

## MVP goal

Given a repository and a task description, the app should identify relevant code, explain the task in repository context, surface open questions, and produce a reviewable implementation plan with file-level evidence.

The product supports two repository inputs:

- GitHub repository: clone it into an isolated workspace.
- Local project: upload it as a ZIP and extract it into an isolated workspace.

Both inputs use the same analysis flow after import. The API can ask Gemini to summarize a task, return open questions and a plan, and cite relevant repository files and line ranges. Once the plan is approved, Agent Lab creates an isolated temporary Git worktree and branch, prepares a bounded file-change draft, shows unified diffs, and applies it only after a separate user confirmation. It does not execute repository code or commands.

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
- Do not run commands or repository code in the current MVP.
- Free-tier provider limits and data policies apply; avoid importing confidential repositories into a demo deployment.

## Current status

The frontend has working GitHub public repository and ZIP imports connected to FastAPI. Both sources create temporary workspaces and display deterministic repository intelligence: languages and framework hints, a path-based project map, API route/framework indicators, database/ORM indicators, and authentication-related dependencies/files. These are evidence-linked static signals, not a verified architecture audit. Task analysis uses Gemini through the backend and returns a summary, assumptions, questions, a plan, verification suggestions, and validated file citations. Plan approval is saved with its analysis record. After approval, the API creates a private temporary checkpoint and isolated Git branch/worktree, then can generate a draft for up to five source files. The UI shows diffs, requires a separate confirmation to apply, and offers rollback when files have not changed since apply. These changes stay in the Agent Lab API workspace; it does not write to the user's original repository or remote GitHub repository. Imported code is not run during analysis or review; verification scripts run only after a separate user action inside a disposable Docker container. Developer chat answers against bounded repository snippets and saves up to 50 turns per workspace. Code Intelligence can run a read-only Gemini review and saves up to 20 review runs. The app has Clerk sign-in/register routes and FastAPI token verification/workspace ownership; Google must be enabled in Clerk and the Clerk environment values configured before accounts are active. See [account setup](docs/USER-AUTH.md). Existing guest workspaces are not automatically transferred into signed-in accounts.

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

To enable accounts, follow [docs/USER-AUTH.md](docs/USER-AUTH.md): copy `apps/web/.env.example` to `apps/web/.env.local`, configure Clerk keys and Google, then set the API Clerk issuer/JWKS and `AUTH_REQUIRED=true`. Until both sides are configured, the demo runs in guest mode.

The API host needs Git installed for public GitHub imports and Docker installed to run verification checks. The API root is `http://localhost:8000/`, with interactive documentation at `/docs` and health check at `/health`. ZIP import uses `POST /api/workspaces/import-zip`, public GitHub import uses `POST /api/workspaces/import-github`, workspace details use `GET /api/workspaces/{workspace_id}` (including saved analyses, chat turns, and code reviews), source previews use `GET /api/workspaces/{workspace_id}/files?path=...`, task analysis uses `POST /api/workspaces/{workspace_id}/tasks/analyze` with `{"task":"..."}`, and plan approval uses `POST /api/workspaces/{workspace_id}/tasks/{analysis_id}/approve`. Approved plans can request drafts with `POST /api/workspaces/{workspace_id}/tasks/{analysis_id}/implementation-draft`, load them with `GET /api/workspaces/{workspace_id}/tasks/implementation-drafts/{draft_id}`, apply them with a separate confirmed `POST /api/workspaces/{workspace_id}/tasks/implementation-drafts/{draft_id}/apply`, and safely revert unchanged applied files with `POST /api/workspaces/{workspace_id}/tasks/implementation-drafts/{draft_id}/rollback`. Verification options use `GET /api/workspaces/{workspace_id}/verification`; run an allowlisted manifest check with `POST /api/workspaces/{workspace_id}/verification` and `{"check_id":"npm:test"}` (the actual check ID is returned by the options endpoint). Checks require an explicit user action and use a temporary copy of non-sensitive source. For supported npm lockfiles, dependency preparation runs in Docker with internet access, npm lifecycle scripts disabled, and npm configured for `registry.npmjs.org`; the selected repository check then runs in a separate network-disabled Docker container. Unsupported or unsafe lockfiles are blocked. Repository chat uses `POST /api/workspaces/{workspace_id}/chat` with `{"message":"..."}`, and Code Intelligence uses `POST /api/workspaces/{workspace_id}/code-intelligence/review`. Source previews are read-only and limited to safe UTF-8 text files up to 1 MB. Analysis, chat, code review, and implementation drafts send bounded text snippets to Gemini and skip common secret/config files; do not upload confidential repositories when using a free-tier key.
