"use client";

import { ChangeEvent, FormEvent, useState } from "react";

type Workspace = {
  id: string;
  name: string;
  source: "GitHub" | "ZIP upload";
  detail: string;
  fileCount?: number;
  filesPreview?: string[];
  projectOverview?: {
    summary: string;
    languages: Array<{ name: string; files: number }>;
    technologies: string[];
    top_level: Array<{ name: string; type: "folder" | "file" }>;
    manifest_files: string[];
  };
};

type Screen = "home" | "project" | "analysis";
type ImportSource = "github" | "zip" | null;
type TaskAnalysis = {
  task_summary: string;
  assumptions: string[];
  questions: string[];
  relevant_files: Array<{ path: string; reason: string; start_line: number; end_line: number }>;
  plan: Array<{ title: string; detail: string; verification: string }>;
  verification: string[];
};

const initialWorkspaces: Workspace[] = [
  { id: "storefront-api", name: "storefront-api", source: "GitHub", detail: "updated 2 hours ago" },
  { id: "task-board", name: "task-board", source: "ZIP upload", detail: "updated yesterday" },
];

const sampleFiles = [
  { name: "src/checkout/checkout.service.ts", kind: "TS", lines: "1–148", reason: "Checkout orchestration and product lookup" },
  { name: "src/products/product.repository.ts", kind: "TS", lines: "1–92", reason: "Database access for product records" },
  { name: "tests/checkout.test.ts", kind: "TS", lines: "1–126", reason: "Existing checkout behavior and regression coverage" },
];

const planSteps = [
  { title: "Trace the checkout request path", detail: "Confirm how cart items reach the checkout service and where product data is loaded.", tag: "Investigate" },
  { title: "Batch product lookups", detail: "Load products in one repository call, then map results back to cart items.", tag: "Implementation" },
  { title: "Cover missing and duplicate products", detail: "Add regression cases for unavailable items and repeated product IDs.", tag: "Verification" },
];

export default function AgentLab() {
  const [screen, setScreen] = useState<Screen>("home");
  const [source, setSource] = useState<ImportSource>(null);
  const [workspaces, setWorkspaces] = useState(initialWorkspaces);
  const [activeWorkspace, setActiveWorkspace] = useState<Workspace>(initialWorkspaces[0]);
  const [repoUrl, setRepoUrl] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [importError, setImportError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [task, setTask] = useState("");
  const [analysisTask, setAnalysisTask] = useState("");
  const [analysisResult, setAnalysisResult] = useState<TaskAnalysis | null>(null);
  const [analysisError, setAnalysisError] = useState("");
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [approved, setApproved] = useState(false);
  const [toast, setToast] = useState("");
  const [activeTab, setActiveTab] = useState<"plan" | "files">("plan");

  function openImport(nextSource: Exclude<ImportSource, null>) {
    setSource(nextSource);
    setRepoUrl("");
    setSelectedFile(null);
    setImportError("");
  }

  function showToast(message: string) {
    setToast(message);
    window.setTimeout(() => setToast(""), 3200);
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    setImportError("");
    if (!file) {
      setSelectedFile(null);
      return;
    }
    if (!file.name.toLowerCase().endsWith(".zip")) {
      setSelectedFile(null);
      setImportError("Choose a .zip archive to continue.");
      return;
    }
    if (file.size > 50 * 1024 * 1024) {
      setSelectedFile(null);
      setImportError("The demo limit is 50 MB per ZIP file.");
      return;
    }
    setSelectedFile(file);
  }

  async function finishImport(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (source === "zip" && !selectedFile) {
      setImportError("Choose a ZIP file first.");
      return;
    }
    setUploading(true);
    setImportError("");
    try {
      const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
      const request = source === "zip"
        ? (() => {
            const formData = new FormData();
            if (selectedFile) formData.append("file", selectedFile);
            return { url: `${apiBase}/api/workspaces/import-zip`, init: { method: "POST", body: formData } };
          })()
        : { url: `${apiBase}/api/workspaces/import-github`, init: { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ repo_url: repoUrl }) } };
      const response = await fetch(request.url, request.init);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Upload failed. Please try again.");
      const nextWorkspace: Workspace = {
        id: payload.id,
        name: payload.name,
        source: payload.source === "GitHub" ? "GitHub" : "ZIP upload",
        detail: "just now",
        fileCount: payload.file_count,
        filesPreview: payload.files_preview,
        projectOverview: payload.project_overview,
      };
      setWorkspaces((items) => [nextWorkspace, ...items]);
      setActiveWorkspace(nextWorkspace);
      setSource(null);
      setScreen("project");
      showToast(`Imported ${payload.file_count} project files from ${payload.source}.`);
    } catch (error) {
      setImportError(error instanceof Error ? error.message : "Could not reach the Agent Lab API.");
    } finally {
      setUploading(false);
    }
  }

  async function startAnalysis(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!task.trim()) return;
    const submittedTask = task.trim();
    setAnalysisTask(submittedTask);
    setApproved(false);
    setActiveTab("plan");
    setAnalysisResult(null);
    setAnalysisError("");
    setScreen("analysis");
    if (!/^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(activeWorkspace.id)) return;
    setAnalysisLoading(true);
    try {
      const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
      const response = await fetch(`${apiBase}/api/workspaces/${activeWorkspace.id}/tasks/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task: submittedTask }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Task analysis failed.");
      setAnalysisResult(payload as TaskAnalysis);
    } catch (error) {
      setAnalysisError(error instanceof Error ? error.message : "Could not reach the Agent Lab API.");
    } finally {
      setAnalysisLoading(false);
    }
  }

  function openWorkspace(workspace: Workspace) {
    setActiveWorkspace(workspace);
    setScreen("project");
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#home" onClick={() => setScreen("home")} aria-label="Agent Lab home"><span className="brand-mark">✳</span><span>agent<span className="brand-light">lab</span></span></a>
        <div className="side-label">WORKSPACE</div>
        <button className={`nav-item ${screen === "home" ? "active" : ""}`} onClick={() => setScreen("home")}><span className="nav-icon">⌂</span>Overview</button>
        <button className={`nav-item ${screen === "project" ? "active" : ""}`} onClick={() => setScreen("home")}><span className="nav-icon">▧</span>Repositories</button>
        <button className={`nav-item ${screen === "analysis" ? "active" : ""}`} onClick={() => setScreen("analysis")}><span className="nav-icon">◷</span>Analysis history</button>
        <div className="sidebar-bottom"><div className="provider-card"><span className="status-dot" /><div><strong>Demo mode</strong><small>Results use sample data</small></div><span className="chevron">⌄</span></div><div className="profile"><div className="avatar">JD</div><div><strong>Developer</strong><small>Personal workspace</small></div><span className="more">···</span></div></div>
      </aside>

      <section className="main-panel">
        <header className="topbar"><div className="breadcrumb"><button onClick={() => setScreen("home")}>Workspace</button><span>/</span>{screen === "home" ? "Overview" : <><button onClick={() => setScreen("project")}>{activeWorkspace.name}</button><span>/</span>{screen === "project" ? "Project overview" : "Task analysis"}</>}</div><div className="top-actions"><span className="demo-chip"><span className="tiny-dot blue-dot" /> INTERACTIVE PREVIEW</span><button className="help-button" aria-label="Help" onClick={() => showToast("This is a frontend-only preview with sample data.")}>?</button></div></header>
        <div className="content">
          {screen === "home" && <HomeScreen workspaces={workspaces} onImport={openImport} onOpen={openWorkspace} />}
          {screen === "project" && <ProjectScreen workspace={activeWorkspace} task={task} onTaskChange={setTask} onAnalyze={startAnalysis} onBack={() => setScreen("home")} />}
          {screen === "analysis" && <AnalysisScreen workspace={activeWorkspace} task={analysisTask} result={analysisResult} error={analysisError} loading={analysisLoading} approved={approved} activeTab={activeTab} onTab={setActiveTab} onApprove={() => setApproved(true)} onBack={() => setScreen("project")} />}
        </div>
      </section>

      {source && <ImportDialog source={source} repoUrl={repoUrl} onRepoUrl={setRepoUrl} selectedFile={selectedFile} onFile={chooseFile} error={importError} uploading={uploading} onDismiss={() => setSource(null)} onSubmit={finishImport} />}
      {toast && <div className="toast" role="status"><span>✓</span>{toast}</div>}
    </main>
  );
}

function HomeScreen({ workspaces, onImport, onOpen }: { workspaces: Workspace[]; onImport: (source: Exclude<ImportSource, null>) => void; onOpen: (workspace: Workspace) => void }) {
  return <>
    <div className="welcome-row"><div><div className="eyebrow"><span className="eyebrow-line" /> YOUR ENGINEERING COPILOT</div><h1>Understand the task.<br /><span>Then write the code.</span></h1><p className="intro">Give Agent Lab a repository and a handoff. Get a clear, evidence-backed plan before implementation begins.</p></div><div className="hero-orbit" aria-hidden="true"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="orbit-core">✳</div><span className="orbit-node node-a">⌘</span><span className="orbit-node node-b">{ }</span><span className="orbit-node node-c">↗</span></div></div>
    <div className="section-heading"><div><h2>Start with a repository</h2><p>Choose where your project lives. Both options lead to the same analysis flow.</p></div><span className="step-pill"><span>01</span> CREATE WORKSPACE</span></div>
    <div className="source-grid">
      <button className="source-card" type="button" onClick={() => onImport("github")}><span className="source-icon github-icon"><GitHubIcon /></span><span className="source-copy"><strong>Connect GitHub</strong><span>Analyze a public repository by URL</span></span><span className="source-arrow">↗</span><span className="source-foot"><span className="tiny-dot green" /> Public repository URL</span></button>
      <button className="source-card" type="button" onClick={() => onImport("zip")}><span className="source-icon upload-icon"><UploadIcon /></span><span className="source-copy"><strong>Upload a project</strong><span>Bring a local project as a ZIP file</span></span><span className="source-arrow">↗</span><span className="source-foot"><span className="tiny-dot blue-dot" /> ZIP files up to 50 MB</span></button>
    </div>
    <div className="how-row"><div className="how-icon">✦</div><div><strong>From handoff to a confident plan</strong><span>Agent Lab maps your code, traces the task to relevant files, and shows its evidence.</span></div><a href="#how-it-works">How the demo works <span>→</span></a></div>
    <section className="recent-section"><div className="section-heading recent-heading"><div><h2>Recent workspaces</h2><p>Pick up where you left off.</p></div><button className="view-all" onClick={() => onImport("github")}>New workspace <span>＋</span></button></div><div className="workspace-list">{workspaces.map((workspace, index) => <button className="workspace-row" onClick={() => onOpen(workspace)} key={workspace.id}><span className={`repo-avatar ${index % 2 ? "blue" : "violet"}`}>{workspace.name.slice(0, 2).toUpperCase()}</span><span className="repo-info"><strong>{workspace.name}</strong><small>{workspace.source} · {workspace.detail}</small></span><span className="repo-status"><span className="tiny-dot green" /> Ready</span><span className="row-arrow">→</span></button>)}</div></section>
    <footer><span>BUILT FOR THE MOMENT BEFORE THE FIRST COMMIT</span><span>Agent Lab <b>·</b> Interactive preview</span></footer>
  </>;
}

function ProjectScreen({ workspace, task, onTaskChange, onAnalyze, onBack }: { workspace: Workspace; task: string; onTaskChange: (task: string) => void; onAnalyze: (event: FormEvent<HTMLFormElement>) => void; onBack: () => void }) {
  return <div className="screen-stack">
    <div className="screen-back"><button onClick={onBack}>← All workspaces</button><span className="demo-label">{workspace.projectOverview ? "IMPORTED REPOSITORY" : "SAMPLE PROJECT DATA"}</span></div>
    <div className="project-title-row"><div><div className="eyebrow"><span className="eyebrow-line" /> PROJECT OVERVIEW</div><h1 className="screen-title">{workspace.name}</h1><p className="screen-subtitle">{workspace.source} workspace · Repository analysis preview</p></div><span className="ready-pill"><span className="tiny-dot green" /> Indexed</span></div>
    <div className="project-stats"><div><span>TECH STACK</span><strong>{workspace.projectOverview?.technologies.length ? workspace.projectOverview.technologies.slice(0, 3).join(" · ") : workspace.projectOverview ? "No framework detected" : "TypeScript · React · Node.js (sample)"}</strong></div><div><span>FILES DISCOVERED</span><strong>{workspace.fileCount ? `${workspace.fileCount} files` : "128 files (sample)"}</strong></div><div><span>SOURCE LANGUAGES</span><strong>{workspace.projectOverview?.languages.slice(0, 2).map((language) => language.name).join(" · ") || (workspace.projectOverview ? "Not detected" : "Sample project")}</strong></div></div>
    <div className="project-columns"><section className="panel-card"><div className="panel-heading"><div><h2>Repository structure</h2><p>{workspace.projectOverview ? "Top-level folders and files" : "Example structure · sample data"}</p></div><span className="panel-dots">···</span></div><div className="tree-list">{workspace.projectOverview ? workspace.projectOverview.top_level.slice(0, 10).map((item) => <div className="tree-row file-path-row" key={item.name}><span className={item.type === "folder" ? "folder-icon" : "file-icon"}>{item.type === "folder" ? "▰" : item.name.split(".").at(-1)?.slice(0, 2).toUpperCase() || "--"}</span><span title={item.name}>{item.name}{item.type === "folder" ? "/" : ""}</span></div>) : workspace.filesPreview ? workspace.filesPreview.slice(0, 6).map((file) => <div className="tree-row file-path-row" key={file}><span className="file-icon">{file.split(".").at(-1)?.slice(0, 2).toUpperCase() || "--"}</span><span title={file}>{file}</span></div>) : <><div className="tree-row"><span>⌄</span><span className="folder-icon">▰</span><strong>src</strong></div><div className="tree-row indent"><span>⌄</span><span className="folder-icon">▰</span><strong>checkout</strong></div><div className="tree-row indent-two"><span className="file-icon">TS</span><span>checkout.service.ts</span><small>148</small></div><div className="tree-row indent"><span>⌄</span><span className="folder-icon">▰</span><strong>products</strong></div><div className="tree-row indent-two"><span className="file-icon">TS</span><span>product.repository.ts</span><small>92</small></div><div className="tree-row"><span>⌄</span><span className="folder-icon">▰</span><strong>tests</strong></div><div className="tree-row indent"><span className="file-icon">TS</span><span>checkout.test.ts</span><small>126</small></div></>}</div><div className="file-preview-list">{workspace.projectOverview && <><div className="list-divider" /><strong className="list-caption">SAMPLE FILE PATHS</strong>{workspace.filesPreview?.slice(0, 5).map((file) => <div className="path-preview" key={file}><span>↳</span>{file}</div>)}</>}</div></section>
      <section className="panel-card overview-card"><div className="panel-heading"><div><h2>Detected technologies</h2><p>{workspace.projectOverview ? "Based on file extensions and manifests" : "Sample overview · placeholder"}</p></div><span className="sparkle">✦</span></div><p className="overview-copy">{workspace.projectOverview?.summary || (workspace.filesPreview ? "Project files imported. No supported language or framework markers were detected." : "A sample Node.js service organized around feature modules. This example shows the intended overview layout.")}</p>{workspace.projectOverview?.languages.length ? <div className="language-list">{workspace.projectOverview.languages.slice(0, 5).map((language) => <div key={language.name}><span>{language.name}</span><strong>{language.files} files</strong><span className="language-meter"><i style={{ width: `${Math.max(8, Math.round(language.files / workspace.projectOverview!.languages[0].files * 100))}%` }} /></span></div>)}</div> : !workspace.projectOverview && <div className="module-chips"><span>Authentication</span><span>Products</span><span>Checkout</span><span>Orders</span></div>}{workspace.projectOverview?.technologies.length ? <div className="module-chips">{workspace.projectOverview.technologies.slice(0, 8).map((technology) => <span key={technology}>{technology}</span>)}</div> : null}<div className="overview-note"><span>i</span> {workspace.projectOverview ? "This is deterministic manifest analysis; AI has not run." : "This sample summary is not based on an imported repository."}</div>{workspace.projectOverview?.manifest_files.length ? <div className="manifest-list"><strong>MANIFESTS</strong>{workspace.projectOverview.manifest_files.slice(0, 4).map((manifest) => <span key={manifest}>{manifest}</span>)}</div> : null}</section></div>
    <form className="task-box" onSubmit={onAnalyze}><div className="task-box-heading"><div className="task-icon">✦</div><div><h2>What task were you handed?</h2><p>Describe the request. Agent Lab will map it to the project and create a plan.</p></div></div><textarea value={task} onChange={(event) => onTaskChange(event.target.value)} placeholder="Example: Checkout sometimes times out when a cart contains many products. Find the bottleneck and suggest a safe fix." rows={3} /><div className="task-box-footer"><span>⌘ ↵ &nbsp; Analyze task</span><button className="primary-button" type="submit" disabled={!task.trim()}>Analyze task <span>→</span></button></div></form>
  </div>;
}

function AnalysisScreen({ workspace, task, result, error, loading, approved, activeTab, onTab, onApprove, onBack }: { workspace: Workspace; task: string; result: TaskAnalysis | null; error: string; loading: boolean; approved: boolean; activeTab: "plan" | "files"; onTab: (tab: "plan" | "files") => void; onApprove: () => void; onBack: () => void }) {
  if (loading || error || result) return <div className="screen-stack">
    <div className="screen-back"><button onClick={onBack}>← {workspace.name}</button><span className="demo-label">{loading ? "ANALYZING REPOSITORY" : result ? "GEMINI ANALYSIS" : "ANALYSIS NEEDS ATTENTION"}</span></div>
    <div className="analysis-title-row"><div><div className="eyebrow"><span className="eyebrow-line" /> TASK ANALYSIS</div><h1 className="screen-title">Task understanding</h1><p className="screen-subtitle">Repository evidence and a proposed implementation plan.</p></div>{result && <span className="confidence-pill">◈ &nbsp; Evidence linked</span>}</div>
    <div className="task-summary"><div className="user-avatar">JD</div><div><span className="summary-label">HANDOFF</span><p>{task}</p></div><button onClick={onBack}>Edit</button></div>
    {loading && <section className="panel-card"><h2>Reading repository context…</h2><p className="overview-copy">Selecting relevant source files and asking Gemini to prepare an evidence-linked plan. No repository code is executed.</p></section>}
    {error && <section className="panel-card"><h2>Could not analyze this task</h2><p className="form-error" role="alert">{error}</p><p className="overview-copy">For imported workspaces, configure GEMINI_API_KEY in <code>apps/api/.env</code>, then restart the API.</p></section>}
    {result && <div className="analysis-grid"><div className="analysis-main">
      <section className="panel-card understanding-card"><div className="panel-heading"><div><h2><span className="sparkle">✦</span> What I understand</h2><p>Generated from the imported repository</p></div><span className="ai-badge">GEMINI</span></div><p className="understanding-copy">{result.task_summary}</p>
        {result.assumptions.map((item, i) => <div className="assumption-box" key={`a-${i}`}><span>i</span><div><strong>Assumption</strong><p>{item}</p></div></div>)}
        {result.questions.map((item, i) => <div className="assumption-box" key={`q-${i}`}><span>?</span><div><strong>Open question</strong><p>{item}</p></div></div>)}
      </section>
      <section className="panel-card plan-card"><div className="panel-heading plan-heading"><div><h2>Investigation &amp; plan</h2><p>Review the suggested steps before implementation begins.</p></div>{approved && <span className="approved-pill">✓ Plan approved</span>}</div><div className="plan-steps">{result.plan.map((step, index) => <div className="plan-step" key={`${step.title}-${index}`}><span className="step-number">{String(index + 1).padStart(2, "0")}</span><div><strong>{step.title}</strong><p>{step.detail}</p><span className="step-tag">Verify: {step.verification}</span></div></div>)}</div>
        {!!result.verification.length && <div className="evidence-list"><strong>Suggested verification</strong>{result.verification.map((item, index) => <p key={index}>{item}</p>)}</div>}
        <div className="plan-footer"><span>Relevant files <strong>{result.relevant_files.length}</strong></span><button className={approved ? "secondary-button" : "primary-button"} onClick={onApprove} disabled={approved}>{approved ? "Plan approved" : "Approve plan"} <span>{approved ? "✓" : "→"}</span></button></div>
      </section>
    </div><aside className="analysis-aside"><section className="panel-card evidence-card"><div className="panel-heading"><div><h2>Code evidence</h2><p>Matching files and line ranges</p></div><span className="evidence-count">{result.relevant_files.length}</span></div>{result.relevant_files.map((file) => <div className="evidence-file compact" key={file.path}><span className="file-icon">{file.path.split(".").at(-1)?.slice(0, 2).toUpperCase() || "--"}</span><div><strong>{file.path}</strong><small>{file.reason}</small><span className="line-ref">L{file.start_line}–{file.end_line}</span></div></div>)}{!result.relevant_files.length && <p className="overview-copy">The model did not return verifiable file citations for this task.</p>}</section><div className="disclaimer"><span>i</span><p>AI suggestions need developer review. Repository contents are treated as untrusted text; code was not executed.</p></div></aside></div>}
  </div>;
  return <div className="screen-stack">
    <div className="screen-back"><button onClick={onBack}>← {workspace.name}</button><span className="demo-label">SAMPLE AI RESULT</span></div>
    <div className="analysis-title-row"><div><div className="eyebrow"><span className="eyebrow-line" /> TASK ANALYSIS</div><h1 className="screen-title">Task understanding</h1><p className="screen-subtitle">Repository evidence and a proposed implementation plan.</p></div><span className="confidence-pill">◈ &nbsp; Evidence linked</span></div>
    <div className="task-summary"><div className="user-avatar">JD</div><div><span className="summary-label">HANDOFF</span><p>{task}</p></div><button aria-label="Edit task" onClick={onBack}>Edit</button></div>
    <div className="analysis-grid"><div className="analysis-main"><section className="panel-card understanding-card"><div className="panel-heading"><div><h2><span className="sparkle">✦</span> What I understand</h2><p>Summary based on sample repository context</p></div><span className="ai-badge">AI SUMMARY</span></div><p className="understanding-copy">The checkout flow may be doing repeated product lookups while processing a cart. The first thing to verify is whether each cart item triggers its own database request, which could increase response time as carts grow.</p><div className="assumption-box"><span>!</span><div><strong>Open question</strong><p>Should checkout fail when a product is missing, or continue with the remaining items?</p></div></div></section>
      <section className="panel-card plan-card"><div className="panel-heading plan-heading"><div><h2>Investigation & plan</h2><p>Review the suggested steps before implementation.</p></div>{approved && <span className="approved-pill">✓ Plan approved</span>}</div><div className="tab-bar"><button className={activeTab === "plan" ? "selected" : ""} onClick={() => onTab("plan")}>Plan <span>3</span></button><button className={activeTab === "files" ? "selected" : ""} onClick={() => onTab("files")}>Relevant files <span>3</span></button></div>
        {activeTab === "plan" ? <div className="plan-steps">{planSteps.map((step, index) => <div className="plan-step" key={step.title}><span className="step-number">0{index + 1}</span><div><strong>{step.title}</strong><p>{step.detail}</p><span className="step-tag">{step.tag}</span></div></div>)}</div> : <div className="evidence-list">{sampleFiles.map((file) => <div className="evidence-file" key={file.name}><span className="file-icon">{file.kind}</span><div><strong>{file.name}</strong><p>{file.reason}</p></div><span className="line-ref">L{file.lines}</span></div>)}</div>}
        <div className="plan-footer"><span>Estimated scope <strong>2–3 files</strong></span><button className={approved ? "secondary-button" : "primary-button"} onClick={onApprove} disabled={approved}>{approved ? "Plan approved" : "Approve plan"} <span>{approved ? "✓" : "→"}</span></button></div>
      </section></div>
      <aside className="analysis-aside"><section className="panel-card evidence-card"><div className="panel-heading"><div><h2>Code evidence</h2><p>Files that support this analysis</p></div><span className="evidence-count">3</span></div>{sampleFiles.map((file) => <div className="evidence-file compact" key={file.name}><span className="file-icon">{file.kind}</span><div><strong>{file.name}</strong><small>{file.reason}</small></div></div>)}<button className="text-button" onClick={() => onTab("files")}>View all relevant files <span>→</span></button></section><section className="panel-card verify-card"><div className="verify-icon">✓</div><div><h3>Suggested verification</h3><p>Run checkout tests and compare query counts for a multi-item cart.</p></div></section><div className="disclaimer"><span>i</span><p>Frontend preview only. These findings are sample content, not an analysis of the selected repository.</p></div></aside></div>
  </div>;
}

function ImportDialog({ source, repoUrl, onRepoUrl, selectedFile, onFile, error, uploading, onDismiss, onSubmit }: { source: Exclude<ImportSource, null>; repoUrl: string; onRepoUrl: (value: string) => void; selectedFile: File | null; onFile: (event: ChangeEvent<HTMLInputElement>) => void; error: string; uploading: boolean; onDismiss: () => void; onSubmit: (event: FormEvent<HTMLFormElement>) => void | Promise<void> }) {
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !uploading) onDismiss(); }}><section className="import-modal" role="dialog" aria-modal="true" aria-labelledby="import-title"><button className="modal-close" onClick={onDismiss} aria-label="Close dialog" disabled={uploading}>×</button><div className={`modal-icon ${source === "github" ? "github-icon" : "upload-icon"}`}>{source === "github" ? <GitHubIcon /> : <UploadIcon />}</div><div className="eyebrow"><span className="eyebrow-line" /> NEW WORKSPACE</div><h2 id="import-title">{source === "github" ? "Connect a repository" : "Upload your project"}</h2><p className="modal-intro">{source === "github" ? "Paste the URL of a public GitHub repository." : "Select a ZIP archive to import it into a temporary workspace."}</p><form onSubmit={onSubmit}>
    {source === "github" ? <label className="field-label">Public GitHub repository URL<input autoFocus type="url" value={repoUrl} onChange={(event) => onRepoUrl(event.target.value)} placeholder="https://github.com/owner/repository" disabled={uploading} /></label> : <label className="drop-zone"><input type="file" accept=".zip,application/zip" onChange={onFile} disabled={uploading} /><span className="drop-icon">↑</span><strong>{selectedFile ? selectedFile.name : "Choose a ZIP file"}</strong><small>{selectedFile ? `${(selectedFile.size / 1024 / 1024).toFixed(1)} MB · selected` : "or drag and drop here · up to 50 MB"}</small><span className="browse-button">Browse files</span></label>}
    {error && <div className="form-error" role="alert">{error}</div>}
    <div className="demo-warning"><span>i</span><p>{source === "github" ? "Only public repositories are supported. The latest revision is cloned; repository code is not executed." : "The archive is extracted on the backend. Generated folders are skipped and workspace files expire after 7 days."}</p></div>
    <div className="modal-actions"><button type="button" className="cancel-button" onClick={onDismiss} disabled={uploading}>Cancel</button><button type="submit" className="primary-button" disabled={uploading || (source === "zip" ? !selectedFile : !repoUrl.trim())}>{uploading ? (source === "github" ? "Cloning repository…" : "Uploading and indexing…") : (source === "github" ? "Clone repository" : "Upload project")}<span>→</span></button></div>
  </form></section></div>;
}

function GitHubIcon() { return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 .9a11.1 11.1 0 0 0-3.51 21.63c.56.1.76-.24.76-.54v-2.1c-3.1.68-3.76-1.32-3.76-1.32-.5-1.29-1.23-1.63-1.23-1.63-1.01-.69.08-.68.08-.68 1.12.08 1.71 1.15 1.71 1.15 1 1.71 2.62 1.22 3.26.93.1-.72.39-1.22.71-1.5-2.47-.28-5.07-1.24-5.07-5.52 0-1.22.44-2.22 1.15-3-.12-.28-.5-1.42.11-2.96 0 0 .94-.3 3.05 1.14a10.6 10.6 0 0 1 5.55 0c2.11-1.44 3.05-1.14 3.05-1.14.61 1.54.23 2.68.11 2.96.72.78 1.15 1.78 1.15 3.01 0 4.29-2.6 5.23-5.08 5.5.4.35.76 1.03.76 2.08v3.08c0 .3.2.65.77.54A11.1 11.1 0 0 0 12 .9Z" /></svg>; }
function UploadIcon() { return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 16V4m0 0L7.5 8.5M12 4l4.5 4.5M5 14v5h14v-5" /></svg>; }
