# MVP product flow and roadmap

## Product promise

Help a developer understand a handed-off software task faster by connecting the task to evidence in the repository and turning it into a concrete, reviewable plan.

## Primary user

A developer who has received a task in an unfamiliar or partially familiar codebase and needs to understand where the work belongs, what is unclear, and how to approach it.

## MVP user journey

1. **Create a workspace**
   - Choose **GitHub repository** or **Upload ZIP**.
   - GitHub input accepts a public repository URL for the first demo. Private repository OAuth is a later enhancement.
   - ZIP upload has size and file-count limits; reject path traversal, symlinks, archives with unsafe entries, and unsupported/binary-heavy content.
2. **Prepare repository context**
   - Clone or safely extract into a per-workspace temporary directory.
   - Exclude generated and irrelevant directories such as `.git`, `node_modules`, build outputs, and vendor folders.
   - Build a lightweight file inventory and searchable text index; do not upload the entire repository indiscriminately to the LLM.
   - Show progress and useful errors (invalid URL/archive, unsupported repository, empty project, provider quota failure).
3. **Show project overview**
   - Display detected languages/framework hints, top-level structure, and a short repository summary.
   - Build a project map from file paths and manifests, with evidence for likely API, database/ORM, and authentication components.
   - Label static findings as signals rather than verified architecture facts and link them to evidence where possible.
4. **Submit a handed-off task**
   - User enters the task description.
   - Agent summarizes the request, identifies acceptance criteria it can infer, and asks focused clarification questions where requirements are missing.
5. **Analyze and plan**
   - Retrieve relevant files and snippets from the local index.
   - Return: task understanding, assumptions/open questions, likely impact areas, ordered implementation steps, and verification suggestions.
   - Each codebase claim should cite a repository-relative path and line range when available. Clearly label unsupported hypotheses.
6. **Review and approve the plan**
   - Approval is recorded against the saved analysis and remains visible when reopening analysis history.
   - Approval is the first gate; by itself it does not edit files or execute commands.
7. **Review and refine**
   - User can ask repository-specific follow-up questions in Developer Chat; answers cite relevant files and lines when available.
   - Save recent chat turns alongside workspace metadata so the conversation reopens with its repository.
   - User can mark the plan as useful or provide feedback.
   - Save recent analysis results with the workspace so they can be reopened after refreshing the browser.
8. **Generate and apply an implementation draft**
   - Only an approved analysis can generate a draft, limited to five files and bounded source context.
   - Show unified diffs and require a separate explicit confirmation before applying.
   - Recheck each file's SHA-256 baseline immediately before writing; reject stale drafts if a file changed.
   - Create an app-managed branch/worktree from the imported snapshot. For ZIP imports, initialize a temporary Git baseline from the safe file inventory.
   - Apply only to the isolated app-managed worktree, never the user's original local repository. Do not run generated code or commands.
   - Offer rollback only while the applied file hashes still match; refuse to overwrite any later developer edits.
   - Store draft source content in the temporary workspace filesystem; persist only draft status and identifiers in workspace metadata.

## MVP acceptance criteria

- Both GitHub URL and ZIP imports lead to the same workspace and analysis experience.
- User can see import/analyze progress and recover from common failures.
- Given a task, the result separates facts supported by repository evidence from assumptions and questions.
- Relevant file references are clickable or otherwise easy to locate.
- Project chat answers are grounded in bounded repository snippets and expose validated file citations.
- Chat history is saved per workspace and limited to the most recent 50 turns.
- The plan has ordered steps and suggested verification, and can be regenerated after user feedback.
- An approved plan can produce a bounded diff draft; applying it requires a separate confirmation and rejects stale file baselines.
- User-triggered lint, test, and syntax checks are selected from a fixed allowlist derived from repository manifests and run in a disposable, resource-limited Docker container with networking disabled and a writable temporary copy of non-sensitive source files.
- The user can review file paths and diffs before any workspace file is changed.
- API keys remain server-side; repository contents are not stored in MongoDB.
- Recent analysis history is persisted with workspace metadata and can be reopened from the UI.
- Imported repository code is never executed during import, analysis, chat, or code review. Verification commands run only after a separate explicit user action and only inside the Docker sandbox. Dependencies are not installed automatically.

## Screen map

1. **Workspace list / start screen** — create a workspace from GitHub or ZIP.
2. **Import and analysis status** — progress, current stage, and actionable errors.
3. **Project overview** — repository summary and structure.
4. **Task analysis** — task input, agent response, evidence references, clarification questions, and plan.
5. **Settings (minimal)** — provider status and demo configuration state; never display secret values.

## Technical boundaries for the first release

- Next.js UI; FastAPI API and orchestration service.
- MongoDB Atlas stores application records only: workspace metadata, analysis records, plans, and conversation state.
- Repository files live in temporary isolated workspace storage and are removed by a documented cleanup policy.
- Start with public GitHub repositories and ZIP upload; do not build GitHub OAuth until private-repository access is required.
- Use a provider interface so Gemini can be swapped without coupling product logic to one SDK.
- Use deterministic file discovery and retrieval before considering embeddings/vector databases.
- No autonomous editing, shell commands, test execution, commit, push, PR creation, multi-agent routing, or self-learning in MVP.

## Expansion roadmap

### Phase 1 — Task understanding MVP

Repository import, lightweight indexing, project overview, task analysis, evidence-backed plan, and feedback loop.

### Phase 2 — Controlled implementation

The demo supports approved plans, bounded drafts, diff review, separate apply confirmation, and stale-file-safe apply/rollback in an app-managed Git branch and worktree. GitHub imports checkpoint their temporary clone; ZIP imports initialize a temporary Git baseline from the safe file inventory. Verification offers only detected npm manifest scripts or built-in Python checks and requires an explicit click. For npm checks, a supported lockfile is required when dependencies are declared; lockfile sources must resolve to the public npm registry. Dependency preparation happens in a disposable Docker container with internet access, npm lifecycle scripts disabled, and npm configured for `registry.npmjs.org`. The selected check runs afterward in a separate network-disabled container. Both stages use a temporary copy with CPU/RAM/process limits, a timeout, and capped output, and the copy is discarded afterward. Docker must be installed on the API host. The feature has not been end-to-end verified against a live Docker daemon. It does not write to the user's original local repository or remote GitHub.

### Phase 3 — Git workflow

Branches, commits, GitHub OAuth/private repositories, push, and pull request creation after user approval.

### Phase 4 — Deeper engineering assistance

Project conventions and decision memory, security/performance/test specialists, verification loop, CI integration, and proactive project health reports.

### Phase 5 — User accounts and workspace ownership

Add account registration and sign-in, then associate imported workspaces and analysis history with the authenticated user. Start by evaluating Google OAuth for low-friction sign-in; consider phone-number OTP as an optional method after selecting an SMS provider and documenting its per-message cost and abuse controls. Keep this separate from GitHub repository authorization, which grants access to private source repositories.

## Decisions to revisit after the MVP demo

- Whether local ZIP is enough or a local companion app is needed for larger projects.
- Whether private GitHub repositories are essential for the target demo.
- Whether MongoDB records should expire automatically or be user-managed.
- Whether repository retrieval needs embeddings after measuring quality with real tasks.
- Whether cloud deployment should run analysis jobs in a separate worker and sandbox service.
