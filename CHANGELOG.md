# Changelog

Notable changes to AI Agent Lab are recorded here.

## Unreleased

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
