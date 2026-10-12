# Changelog

Notable changes to AI Agent Lab are recorded here.

## Unreleased

- Added a dependency readiness API and System health screen for storage, MongoDB, Gemini, Clerk, Git, and the Docker daemon without exposing secret values.
- Improved verification failures with a compact list of lint/build diagnostics while retaining the full command output with terminal color codes removed for review and copying.
- Added Vercel configuration for the Next.js web project, documented the separate persistent API deployment, and removed runtime font downloads.
- Declared the Next.js ESLint React Hooks plugin directly so clean pnpm installs can complete production linting.
- Kept Clerk JWKS network refreshes off FastAPI's event loop and ensured MongoDB clients close after metadata write failures.
- Corrected account documentation to describe automatic claiming of same-browser guest workspaces after sign-in.
- Fixed Code Intelligence repository context selection to scan the imported workspace root, so review requests can read the intended source tree.
- Added a plan feedback form that reruns analysis with the requested refinements and saves the revised result as a separate history entry.
- Added server-paginated, searchable analysis history for signed-in users, with MongoDB aggregation and a filesystem fallback; added retryable, actionable API connection errors in the workspace view.
- Standardized temporary Gemini `503` handling across task analysis, chat, code review, and implementation draft generation.
- Added read-only Git branch and changed-path status for imported workspaces; sensitive paths are omitted and no commit or push is performed.
- Added frontend render smoke coverage and a repeatable standalone web validation plan.
- Made the API reject apply/rollback requests that do not include explicit confirmation.
- Hardened Clerk authorized-party validation and kept the frontend in guest mode when only one Clerk key is configured.
- Expanded verification profiling for npm workspaces and mixed Node/Python repositories; report unsupported package managers and unsafe/missing dependencies as blocked.
- Disabled dependency-install networking by default and added an explicit API opt-in for npm Docker bridge egress.
- Check the Docker daemon, not only the Docker CLI, before enabling repository verification.
- Added API safety/profile tests and tightened file/line validation for code-review citations.
- Added an owner-filtered workspace listing API and kept guest workspace IDs browser-local so guest workspaces are not globally enumerable.
- Added evidence-backed static project-health indicators for tests, CI, deployment/container configuration, documentation, and dependency lockfiles without inventing a score.
- Documented API routes, data persistence, demo limits, verification policy, and current product gaps.
- Clarified that Docker dependency networking is opt-in and updated verification wording consistently.
- Added GitHub Actions CI for API unit tests and web typecheck, lint, tests, and build on main and Codex branches.
- Declared `python-dotenv` as a direct API dependency because the API loads its `.env` file explicitly.
