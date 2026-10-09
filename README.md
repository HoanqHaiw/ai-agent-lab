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

See the [API reference](docs/API.md), [architecture and trust boundaries](docs/ARCHITECTURE.md), and [changelog](CHANGELOG.md).

## Configuration principles

- Keep `GEMINI_API_KEY`, `MONGODB_URI`, and any GitHub credentials on the backend.
- Never commit secrets or expose provider keys in browser code.
- Treat imported repositories and their contents as untrusted input.
- Do not run commands or repository code in the current MVP.
- Free-tier provider limits and data policies apply; avoid importing confidential repositories into a demo deployment.

## Current status

The frontend has working GitHub public repository and ZIP imports connected to FastAPI. Both sources create temporary workspaces and display deterministic repository intelligence: languages and framework hints, a path-based project map, API route/framework indicators, database/ORM indicators, authentication-related dependencies/files, and file-presence signals for tests, CI, deployment/container configuration, documentation, and dependency lockfiles. These are evidence-linked static signals, not a verified architecture audit or quality/security/coverage score. Task analysis uses Gemini through the backend and returns a summary, assumptions, questions, a plan, verification suggestions, and validated file citations. Plan approval is saved with its analysis record. After approval, the API creates a private temporary checkpoint and isolated Git branch/worktree, then can generate a draft for up to five source files. The UI shows diffs, requires a separate confirmation to apply, and offers rollback when files have not changed since apply. These changes stay in the Agent Lab API workspace; it does not write to the user's original repository or remote GitHub repository. Imported code is not run during analysis or review; verification scripts run only after a separate user action inside a disposable Docker container. Developer chat answers against bounded repository snippets and saves up to 50 turns per workspace. Code Intelligence can run a read-only Gemini review and saves up to 20 review runs. The app has Clerk sign-in/register routes and FastAPI token verification/workspace ownership; Google must be enabled in Clerk and the Clerk environment values configured before accounts are active. See [account setup](docs/USER-AUTH.md). Existing guest workspaces are not automatically transferred into signed-in accounts.

## Run locally

Web app:

```powershell
cd apps/web
pnpm install --frozen-lockfile
pnpm dev
```

Run the web checks from `apps/web`:

```powershell
pnpm lint
pnpm build
pnpm test
```

API (Python 3.11+):

```powershell
cd apps/api
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Run API safety/profile tests from `apps/api` after activating the venv:

```powershell
python -m unittest discover -s tests -v
```

GitHub Actions runs the API tests and web typecheck, lint, tests, and production build for pushes to `main` or `codex/**` branches, and for pull requests targeting `main` (`.github/workflows/ci.yml`).

Copy `apps/api/.env.example` to `apps/api/.env`, then set `GEMINI_API_KEY` to a key from Google AI Studio. The key stays on the backend. `GEMINI_MODEL` defaults to `gemini-3.8-flash`. Set `MONGODB_URI` to use Atlas for workspace metadata; without it, metadata is stored beside the workspace on the API host. Repository files and implementation drafts stay on the API filesystem under `WORKSPACE_ROOT` (default `apps/api/.workspaces`) and expire after seven days. MongoDB does not persist repository contents. A deployed API therefore needs a durable volume at `WORKSPACE_ROOT`; a MongoDB URI alone does not make repository workspaces durable or shareable between instances. Configure `NEXT_PUBLIC_API_BASE_URL` in `apps/web/.env.local` if the API is not running on `http://localhost:8000`.

To enable accounts, follow [docs/USER-AUTH.md](docs/USER-AUTH.md): copy `apps/web/.env.example` to `apps/web/.env.local`, configure Clerk keys and Google, then set the API Clerk issuer/JWKS and `AUTH_REQUIRED=true`. Until both sides are configured, the demo runs in guest mode.

The API host needs Git for public GitHub imports and Docker for verification. The API root is `http://localhost:8000/`, interactive documentation is at `/docs`, and `/health` is a process health check (not a dependency/readiness check). ZIP import uses `POST /api/workspaces/import-zip`; public GitHub import uses `POST /api/workspaces/import-github`. `GET /api/workspaces` lists unexpired workspaces for the current account. Guest mode keeps workspace IDs only in that browser and does not expose a global guest-workspace listing. Workspace details include saved analyses, chat turns, and code reviews. The remaining API routes are documented by FastAPI at `/docs`.

Verification is deliberately limited. It offers manifest-declared `lint`, `typecheck`, `test`, and `build` scripts from npm packages, using each package's working directory and a declared npm workspace root when applicable. Python projects get a syntax check; Python test suites are explicitly blocked until dependency preparation can be made safe. npm lockfiles must be supported versions 1–3 and resolve package URLs to `registry.npmjs.org`. pnpm, Yarn, and Bun are detected but dependency preparation is currently blocked rather than incorrectly invoking npm. Checks that need npm dependencies are blocked by default; setting `VERIFICATION_ALLOW_DEPENDENCY_NETWORK=true` explicitly allows Docker bridge networking during `npm ci`. That is **not** hostname-restricted egress, despite lockfile validation; use it only for trusted demo repositories. npm lifecycle scripts are disabled, and the selected check runs afterward in a separate network-disabled, read-only, resource-limited Docker container. The temporary copy is discarded. Checks without dependencies do not need this setting. The options endpoint also checks that the Docker daemon is reachable, not just that a CLI executable exists.

ZIP imports are limited to 50 MB compressed, 200 MB extracted, and 10,000 archive entries. GitHub imports are limited to 200 MB of supported checked-out files and 10,000 files after cloning. Source previews are limited to safe UTF-8 files up to 1 MB; model context is bounded and common secret/config files are skipped. Imports, analysis, chat, reviews, drafts, and workspace files are stored on the API host; do not upload confidential repositories when using a free-tier Gemini key.

This repository has no production deployment manifests, deployment pipeline, object-storage backend, rate limiting, or multi-instance workspace storage. Treat it as a local/demo application until those operational controls are implemented and reviewed. For a hosted deployment, require authentication, configure Clerk/Google and Gemini server-side, persist `WORKSPACE_ROOT`, protect it with TLS/network policies and backups, and define retention and data-handling policy.
