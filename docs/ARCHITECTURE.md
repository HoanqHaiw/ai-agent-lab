# Architecture and trust boundaries

## Runtime components

```text
Browser
  └─ Next.js / React UI
       ├─ Clerk sign-in and session token (optional until configured)
       └─ FastAPI API
            ├─ Workspace/import and temporary filesystem storage
            ├─ Static project overview and bounded file retrieval
            ├─ Gemini task analysis, chat, review, and draft generation
            ├─ App-managed Git checkpoint/worktree for reviewed changes
            ├─ Docker-based verification runner
            └─ Optional MongoDB metadata persistence
```

The browser never receives `GEMINI_API_KEY` or `MONGODB_URI`. FastAPI verifies Clerk session tokens and checks workspace ownership for workspace routes. Public GitHub imports use `git` on the API host; private-repository OAuth and pushes are not implemented.

## Data lifecycle

1. GitHub public repositories are shallow-cloned, or ZIP files are extracted, into a workspace under `WORKSPACE_ROOT`.
2. A bounded file inventory and deterministic project overview are created, including evidence-backed file-presence signals for tests, CI, deployment/container configuration, docs, and dependency lockfiles. These signals do not produce a quality/security score. MongoDB, when configured, stores workspace metadata and history, not source files. Authenticated workspaces are listed through an owner-filtered API; guest browsers store only their own workspace IDs locally, and the API does not enumerate anonymous workspaces.
3. Task/chat/review/draft requests send selected, line-numbered text snippets to Gemini. Common secret/config files are excluded; this is a demo filter, not a guarantee that every secret format is recognized.
4. Approved implementation changes are written only to an app-managed branch/worktree. Apply and rollback require explicit confirmations; both check file hashes to avoid overwriting changed files.
5. Workspace data expires after seven days. The default filesystem location is local to the API process, so a hosted or multi-instance deployment needs durable shared storage that is not provided here.

## Verification boundary

Only manifest-declared npm `lint`, `typecheck`, `test`, and `build` scripts, plus a built-in Python syntax check, are exposed. Each check runs in a temporary source snapshot with CPU, memory, process, time, and output limits, a non-root user, dropped Linux capabilities, and no network.

npm dependencies require a supported lockfile (npm lock versions 1–3) with dependency URLs restricted to `registry.npmjs.org`. The API blocks installation by default. Setting `VERIFICATION_ALLOW_DEPENDENCY_NETWORK=true` allows Docker bridge egress for `npm ci`; Docker does not enforce a hostname allowlist, so this mode is limited to trusted demo repositories. npm lifecycle scripts are disabled. pnpm, Yarn, Bun, and Python test dependency installation are identified but blocked until their locked preparation can be safely supported.

The container uses the host Docker daemon; Docker isolation is not a substitute for a hardened multi-tenant sandbox. Do not expose the demo API to untrusted users without additional isolation, rate limits, resource controls, and security review.

## Current non-goals / missing components

There is no generic terminal/tool API, user-facing Git commit/push/PR workflow, CI result integration in the app, private GitHub access, multi-agent orchestrator, curated project memory, document-generation workflow, project health dashboard, production deployment manifest, object storage, or multi-instance workspace service. GitHub Actions currently runs the API and web checks on pushes to `main`/`codex/**` and pull requests targeting `main`. The current Gemini review is static and bounded; it is not an independent verifier, CVE scanner, test-coverage report, or performance measurement.
