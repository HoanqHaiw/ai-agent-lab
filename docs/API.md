# API reference

FastAPI owns workspace metadata and repository-file access. For interactive OpenAPI details, run the API and open `/docs`; the schema is also available at `/openapi.json`.

## Authentication

Send `Authorization: Bearer <Clerk session token>` when signed in. The API verifies the signature using Clerk JWKS, issuer, expiration, subject, and authorized party. Set `AUTH_REQUIRED=true` to require a token in local development; production environments require it by default unless explicitly overridden. Workspace reads and mutations return `404` when the authenticated user does not own the workspace.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Service information and links to API docs and health. |
| `GET` | `/health` | Process health only; it does not check MongoDB, Gemini, Git, or Docker readiness. |
| `GET` | `/api/workspaces` | List unexpired workspaces owned by the authenticated user. Guest mode returns an empty list to avoid disclosing anonymous workspaces. |
| `POST` | `/api/workspaces/import-zip` | Multipart form upload with a `.zip` field named `file`. |
| `POST` | `/api/workspaces/import-github` | Import a public GitHub repository. |
| `GET` | `/api/workspaces/{workspace_id}` | Workspace overview, static project-health signals, and saved analyses, chat, reviews, and implementation state. |
| `GET` | `/api/workspaces/{workspace_id}/files?path=...` | Read a safe UTF-8 source preview. |
| `POST` | `/api/workspaces/{workspace_id}/tasks/analyze` | Analyze a task against bounded repository context. |
| `POST` | `/api/workspaces/{workspace_id}/tasks/{analysis_id}/approve` | Save user approval of an analysis plan. |
| `POST` | `/api/workspaces/{workspace_id}/tasks/{analysis_id}/implementation-draft` | Generate a bounded draft from an approved plan. |
| `GET` | `/api/workspaces/{workspace_id}/tasks/implementation-drafts/{draft_id}` | Read a draft and its reviewable diffs. |
| `POST` | `/api/workspaces/{workspace_id}/tasks/implementation-drafts/{draft_id}/apply` | Apply a pending draft after explicit confirmation. |
| `POST` | `/api/workspaces/{workspace_id}/tasks/implementation-drafts/{draft_id}/rollback` | Revert an applied draft after explicit confirmation and hash checks. |
| `POST` | `/api/workspaces/{workspace_id}/chat` | Ask a repository-grounded question and save the answer. |
| `POST` | `/api/workspaces/{workspace_id}/code-intelligence/review` | Run and save a bounded read-only static review. |
| `GET` | `/api/workspaces/{workspace_id}/verification` | List detected checks, blocked checks, dependency policy, and sandbox availability. |
| `POST` | `/api/workspaces/{workspace_id}/verification` | Run one allowlisted check by its returned `check_id`. |

### Request examples

```json
{ "repo_url": "https://github.com/owner/public-repository" }
```

```json
{ "task": "Add validation for the checkout request and cover it with tests." }
```

```json
{ "message": "Where is the checkout request validated?" }
```

```json
{ "check_id": "npm:1:test" }
```

Apply and rollback require `{"confirmed": true}` in the request body, in addition to the separate confirmation prompt in the web UI. Draft application also requires an approved analysis, the linked pending draft, and unchanged source hashes.

Errors use a JSON `detail` string and an HTTP status. Common statuses include `401` for missing/invalid authentication, `404` for absent or non-owned workspaces, `409` for stale state or missing confirmation, `413` for size limits, `422` for invalid input, and `503` for missing provider/sandbox configuration.

## Data and execution boundaries

- ZIP limits: 50 MB compressed, 200 MB extracted, 10,000 entries.
- GitHub imports accept public `github.com/owner/repository` URLs; checked-out supported files are limited to 200 MB and 10,000 entries.
- Source previews are limited to safe UTF-8 files up to 1 MB. AI requests contain bounded text snippets and validated file/line references, not the full repository.
- Workspace metadata may be stored in MongoDB. Repository files and implementation drafts remain on the API filesystem in `WORKSPACE_ROOT`; they expire after seven days.
- The authenticated workspace list is loaded from the API and filtered by owner. Guest browsers remember only their own workspace IDs locally; the API never enumerates guest workspaces. MongoDB alone does not make repository files available across API instances.
- Imported code is not executed during import, analysis, chat, or review. Verification requires an explicit request and runs in Docker. See [MVP flow and security boundaries](./MVP-FLOW.md) for package-manager and network restrictions.
