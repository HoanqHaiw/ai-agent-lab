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
   - Label inferred details as estimates and link them to evidence where possible.
4. **Submit a handed-off task**
   - User enters the task description.
   - Agent summarizes the request, identifies acceptance criteria it can infer, and asks focused clarification questions where requirements are missing.
5. **Analyze and plan**
   - Retrieve relevant files and snippets from the local index.
   - Return: task understanding, assumptions/open questions, likely impact areas, ordered implementation steps, and verification suggestions.
   - Each codebase claim should cite a repository-relative path and line range when available. Clearly label unsupported hypotheses.
6. **Review and refine**
   - User can ask follow-up questions or request a revised plan.
   - User marks the plan as useful or provides feedback. No code is changed in this phase.

## MVP acceptance criteria

- Both GitHub URL and ZIP imports lead to the same workspace and analysis experience.
- User can see import/analyze progress and recover from common failures.
- Given a task, the result separates facts supported by repository evidence from assumptions and questions.
- Relevant file references are clickable or otherwise easy to locate.
- The plan has ordered steps and suggested verification, and can be regenerated after user feedback.
- API keys remain server-side; repository contents are not stored in MongoDB.
- Imported repository code is never executed by the MVP.

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

Approval gate, isolated writable worktree, file edits, diff review, explicit command allowlist, test/lint execution in a container, and rollback.

### Phase 3 — Git workflow

Branches, commits, GitHub OAuth/private repositories, push, and pull request creation after user approval.

### Phase 4 — Deeper engineering assistance

Project conventions and decision memory, security/performance/test specialists, verification loop, CI integration, and proactive project health reports.

## Decisions to revisit after the MVP demo

- Whether local ZIP is enough or a local companion app is needed for larger projects.
- Whether private GitHub repositories are essential for the target demo.
- Whether MongoDB records should expire automatically or be user-managed.
- Whether repository retrieval needs embeddings after measuring quality with real tasks.
- Whether cloud deployment should run analysis jobs in a separate worker and sandbox service.
