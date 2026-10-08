"use client";

import { ChangeEvent, FormEvent, useEffect, useState } from "react";
import { useCallback, type ReactNode } from "react";
import { UserButton, useAuth } from "@clerk/nextjs";
import Link from "next/link";

type ApiFetch = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;
const clerkClientEnabled = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);
const localApiFetch: ApiFetch = (input, init) => fetch(input, init);

type Workspace = {
  id: string;
  name: string;
  source: "GitHub" | "ZIP upload";
  detail: string;
  fileCount?: number;
  filesPreview?: string[];
  analyses?: AnalysisRecord[];
  chatMessages?: ChatTurn[];
  codeReviews?: CodeReviewRecord[];
  implementationBranch?: string;
  projectOverview?: {
    summary: string;
    languages: Array<{ name: string; files: number }>;
    technologies: string[];
    top_level: Array<{ name: string; type: "folder" | "file" }>;
    manifest_files: string[];
    architecture: { style: string; project_map: Array<{ name: string; file_count: number; evidence_files: string[] }> };
    database: { systems: string[]; evidence_files: string[] };
    api: { frameworks: string[]; route_files: string[] };
    authentication: { signals: string[]; evidence_files: string[] };
  };
};

type Screen = "home" | "project" | "analysis" | "history" | "chat" | "intelligence" | "verification";
type ImportSource = "github" | "zip" | null;
type TaskAnalysis = {
  task_summary: string;
  assumptions: string[];
  questions: string[];
  relevant_files: Array<{ path: string; reason: string; start_line: number; end_line: number }>;
  plan: Array<{ title: string; detail: string; verification: string }>;
  verification: string[];
};
type AnalysisRecord = { id: string; task: string; created_at: string; approved_at?: string; implementation_draft_id?: string; implementation_status?: string; result: TaskAnalysis };
type ImplementationDraft = { id: string; analysis_id: string; summary: string; status: "pending_review" | "applied" | "stale" | "reverted"; created_at: string; files: Array<{ path: string; action: "create" | "update"; rationale: string; diff: string }> };
type ChatTurn = { id: string; question: string; answer: string; relevant_files: TaskAnalysis["relevant_files"]; created_at: string };
type CodeReview = { review_summary: string; dependency_notes: string[]; findings: Array<{ category: string; severity: string; title: string; description: string; file: string; start_line: number; end_line: number; recommendation: string }>; files_reviewed: string[]; scope_note: string };
type CodeReviewRecord = { id: string; created_at: string; result: CodeReview };
type EvidencePreview = { path: string; content: string; startLine: number; endLine: number };

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

function workspaceStorageKey(userId: string | null) {
  return userId ? `agent-lab.workspace-ids.${userId}` : "agent-lab.workspace-ids";
}

function rememberWorkspace(id: string, userId: string | null) {
  try {
    const storageKey = workspaceStorageKey(userId);
    const saved: unknown = JSON.parse(window.localStorage.getItem(storageKey) || "[]");
    const ids = Array.isArray(saved) ? saved.filter((item): item is string => typeof item === "string") : [];
    window.localStorage.setItem(storageKey, JSON.stringify([id, ...ids.filter((item) => item !== id)].slice(0, 20)));
  } catch {
    // The workspace remains usable for this session when browser storage is unavailable.
  }
}

export default function AgentLab() {
  return clerkClientEnabled ? <AuthenticatedAgentLab /> : <AgentLabWorkspace apiFetch={localApiFetch} userId={null} accountSlot={null} />;
}

function AuthenticatedAgentLab() {
  const { getToken, userId } = useAuth();
  const apiFetch = useCallback<ApiFetch>(async (input, init = {}) => {
    const token = await getToken();
    const headers = new Headers(init.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return fetch(input, { ...init, headers });
  }, [getToken]);
  return <AgentLabWorkspace apiFetch={apiFetch} userId={userId || null} accountSlot={<UserButton />} />;
}

function AgentLabWorkspace({ apiFetch, userId, accountSlot }: { apiFetch: ApiFetch; userId: string | null; accountSlot: ReactNode }) {
  const emptyWorkspace: Workspace = { id: "", name: "Select a repository", source: "ZIP upload", detail: "no workspace selected" };
  const [screen, setScreen] = useState<Screen>("home");
  const [source, setSource] = useState<ImportSource>(null);
  const [workspaces, setWorkspaces] = useState<Workspace[]>(() => userId ? [] : initialWorkspaces);
  const [activeWorkspace, setActiveWorkspace] = useState<Workspace>(() => userId ? emptyWorkspace : initialWorkspaces[0]);
  const [repoUrl, setRepoUrl] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [importError, setImportError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [task, setTask] = useState("");
  const [analysisTask, setAnalysisTask] = useState("");
  const [analysisResult, setAnalysisResult] = useState<TaskAnalysis | null>(null);
  const [analysisRecordId, setAnalysisRecordId] = useState("");
  const [implementationDraftId, setImplementationDraftId] = useState("");
  const [implementationStatus, setImplementationStatus] = useState("");
  const [analysisError, setAnalysisError] = useState("");
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [approved, setApproved] = useState(false);
  const [approvalLoading, setApprovalLoading] = useState(false);
  const [approvalError, setApprovalError] = useState("");
  const [toast, setToast] = useState("");
  const [activeTab, setActiveTab] = useState<"plan" | "files">("plan");

  useEffect(() => {
    let savedIds: unknown;
    try {
      savedIds = JSON.parse(window.localStorage.getItem(workspaceStorageKey(userId)) || "[]");
    } catch {
      return;
    }
    if (!Array.isArray(savedIds) || !savedIds.length) return;
    const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
    Promise.all(savedIds.filter((id): id is string => typeof id === "string").map(async (id): Promise<Workspace | null> => {
      try {
        const response = await apiFetch(`${apiBase}/api/workspaces/${id}`);
        if (!response.ok) return null;
        const payload = await response.json();
        return {
          id: payload.id,
          name: payload.name,
          source: payload.source === "GitHub" ? "GitHub" as const : "ZIP upload" as const,
          detail: "saved workspace",
          fileCount: payload.file_count,
          filesPreview: payload.files_preview,
          projectOverview: payload.project_overview,
          analyses: payload.analyses || [],
          chatMessages: payload.chat_messages || [],
          codeReviews: payload.code_reviews || [],
          implementationBranch: payload.implementation_branch,
        } satisfies Workspace;
      } catch {
        return null;
      }
    })).then((restored) => {
      const valid = restored.filter((item): item is Workspace => item !== null);
      if (!valid.length) return;
      setWorkspaces(userId ? valid : [...valid, ...initialWorkspaces]);
      setActiveWorkspace(valid[0]);
    });
  }, [apiFetch, userId]);

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
      const response = await apiFetch(request.url, request.init);
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
        analyses: payload.analyses || [],
        chatMessages: payload.chat_messages || [],
        codeReviews: payload.code_reviews || [],
      };
      setWorkspaces((items) => [nextWorkspace, ...items]);
      rememberWorkspace(payload.id, userId);
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
    setAnalysisRecordId("");
    setImplementationDraftId("");
    setImplementationStatus("");
    setAnalysisError("");
    setApprovalError("");
    setScreen("analysis");
    if (!/^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(activeWorkspace.id)) return;
    setAnalysisLoading(true);
    try {
      const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
      const response = await apiFetch(`${apiBase}/api/workspaces/${activeWorkspace.id}/tasks/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task: submittedTask }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Task analysis failed.");
      const record = payload as AnalysisRecord;
      setAnalysisResult(record.result);
      setAnalysisRecordId(record.id);
      setImplementationDraftId(record.implementation_draft_id || "");
      setImplementationStatus(record.implementation_status || "");
      setApproved(Boolean(record.approved_at));
      const updatedWorkspace = { ...activeWorkspace, analyses: [record, ...(activeWorkspace.analyses || []).filter((item) => item.id !== record.id)].slice(0, 20) };
      setActiveWorkspace(updatedWorkspace);
      setWorkspaces((items) => items.map((item) => item.id === updatedWorkspace.id ? updatedWorkspace : item));
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

  function updateChatMessages(workspaceId: string, messages: ChatTurn[]) {
    setWorkspaces((items) => items.map((item) => item.id === workspaceId ? { ...item, chatMessages: messages } : item));
    setActiveWorkspace((item) => item.id === workspaceId ? { ...item, chatMessages: messages } : item);
  }

  function updateCodeReviews(workspaceId: string, reviews: CodeReviewRecord[]) {
    setWorkspaces((items) => items.map((item) => item.id === workspaceId ? { ...item, codeReviews: reviews } : item));
    setActiveWorkspace((item) => item.id === workspaceId ? { ...item, codeReviews: reviews } : item);
  }

  function rememberImplementationDraft(id: string) {
    setImplementationDraftId(id);
    setImplementationStatus("pending_review");
    const updated = { ...activeWorkspace, analyses: (activeWorkspace.analyses || []).map((record) => record.id === analysisRecordId ? { ...record, implementation_draft_id: id, implementation_status: "pending_review" } : record) };
    setActiveWorkspace(updated);
    setWorkspaces((items) => items.map((item) => item.id === updated.id ? updated : item));
    void refreshWorkspaceDetails();
  }

  function updateImplementationState(status: "applied" | "reverted") {
    setImplementationStatus(status);
    const updated = { ...activeWorkspace, analyses: (activeWorkspace.analyses || []).map((record) => record.id === analysisRecordId ? { ...record, implementation_status: status } : record) };
    setActiveWorkspace(updated);
    setWorkspaces((items) => items.map((item) => item.id === updated.id ? updated : item));
    void refreshWorkspaceDetails();
  }

  async function refreshWorkspaceDetails() {
    try {
      const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
      const response = await apiFetch(`${apiBase}/api/workspaces/${activeWorkspace.id}`);
      if (!response.ok) return;
      const payload = await response.json();
      setActiveWorkspace((current) => current.id === payload.id ? { ...current, fileCount: payload.file_count, filesPreview: payload.files_preview, projectOverview: payload.project_overview, implementationBranch: payload.implementation_branch } : current);
      setWorkspaces((items) => items.map((item) => item.id === payload.id ? { ...item, fileCount: payload.file_count, filesPreview: payload.files_preview, projectOverview: payload.project_overview, implementationBranch: payload.implementation_branch } : item));
    } catch {
      // The applied files remain available from the API even if refreshing the overview fails.
    }
  }

  async function approveCurrentPlan() {
    if (approvalLoading || approved) return;
    setApprovalError("");
    if (!analysisRecordId || !/^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(activeWorkspace.id)) {
      setApproved(true);
      return;
    }
    setApprovalLoading(true);
    try {
      const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
      const response = await apiFetch(`${apiBase}/api/workspaces/${activeWorkspace.id}/tasks/${analysisRecordId}/approve`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Could not save plan approval.");
      setApproved(true);
      const updatedWorkspace = { ...activeWorkspace, analyses: (activeWorkspace.analyses || []).map((item) => item.id === payload.id ? payload as AnalysisRecord : item) };
      setActiveWorkspace(updatedWorkspace);
      setWorkspaces((items) => items.map((item) => item.id === updatedWorkspace.id ? updatedWorkspace : item));
    } catch (cause) {
      setApprovalError(cause instanceof Error ? cause.message : "Could not save plan approval.");
    } finally {
      setApprovalLoading(false);
    }
  }

  function openAnalysis(workspace: Workspace, record: AnalysisRecord) {
    setActiveWorkspace(workspace);
    setTask(record.task);
    setAnalysisTask(record.task);
    setAnalysisResult(record.result);
    setAnalysisRecordId(record.id);
    setImplementationDraftId(record.implementation_draft_id || "");
    setImplementationStatus(record.implementation_status || "");
    setAnalysisError("");
    setApprovalError("");
    setAnalysisLoading(false);
    setApproved(Boolean(record.approved_at));
    setActiveTab("plan");
    setScreen("analysis");
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#home" onClick={() => setScreen("home")} aria-label="Agent Lab home"><span className="brand-mark">✳</span><span>agent<span className="brand-light">lab</span></span></a>
        <div className="side-label">WORKSPACE</div>
        <button className={`nav-item ${screen === "home" ? "active" : ""}`} onClick={() => setScreen("home")}><span className="nav-icon">⌂</span>Overview</button>
        <button className={`nav-item ${screen === "project" || screen === "verification" ? "active" : ""}`} onClick={() => setScreen("home")}><span className="nav-icon">▧</span>Repositories</button>
        <button className={`nav-item ${screen === "chat" ? "active" : ""}`} onClick={() => setScreen("chat")}><span className="nav-icon">✧</span>Developer chat</button>
        <button className={`nav-item ${screen === "intelligence" ? "active" : ""}`} onClick={() => setScreen("intelligence")}><span className="nav-icon">⌕</span>Code intelligence</button>
        <button className={`nav-item ${screen === "history" ? "active" : ""}`} onClick={() => setScreen("history")}><span className="nav-icon">◷</span>Analysis history</button>
        <div className="sidebar-bottom"><div className="provider-card"><span className="status-dot" /><div><strong>Gemini API</strong><small>Key stays on backend</small></div><span className="chevron">⌄</span></div><div className="profile">{accountSlot || <><div className="avatar">G</div><div><strong>Guest</strong><small>Local demo mode</small></div><Link className="profile-auth-link" href="/login">Sign in</Link></>}</div></div>
      </aside>

      <section className="main-panel">
        <header className="topbar"><div className="breadcrumb"><button onClick={() => setScreen("home")}>Workspace</button><span>/</span>{screen === "home" ? "Overview" : screen === "history" ? "Analysis history" : <><button onClick={() => setScreen("project")}>{activeWorkspace.name}</button><span>/</span>{screen === "project" ? "Project overview" : screen === "chat" ? "Developer chat" : screen === "intelligence" ? "Code intelligence" : screen === "verification" ? "Verification" : "Task analysis"}</>}</div><div className="top-actions"><span className="demo-chip"><span className="tiny-dot blue-dot" /> INTERACTIVE PREVIEW</span><button className="help-button" aria-label="Help" onClick={() => showToast("This is a preview with an API-connected analysis flow.")}>?</button></div></header>
        <div className="content">
          {screen === "home" && <HomeScreen workspaces={workspaces} onImport={openImport} onOpen={openWorkspace} />}
          {screen === "project" && <ProjectScreen workspace={activeWorkspace} task={task} onTaskChange={setTask} onAnalyze={startAnalysis} onBack={() => setScreen("home")} onOpenChat={() => setScreen("chat")} onRunReview={() => setScreen("intelligence")} onRunVerification={() => setScreen("verification")} />}
          {screen === "analysis" && <AnalysisScreen apiFetch={apiFetch} workspace={activeWorkspace} analysisId={analysisRecordId} draftId={implementationDraftId} implementationStatus={implementationStatus} onDraftCreated={rememberImplementationDraft} onImplementationChanged={updateImplementationState} task={analysisTask} result={analysisResult} error={analysisError} loading={analysisLoading} approved={approved} approvalLoading={approvalLoading} approvalError={approvalError} activeTab={activeTab} onTab={setActiveTab} onApprove={() => void approveCurrentPlan()} onBack={() => setScreen("project")} />}
          {screen === "chat" && <ChatScreen key={activeWorkspace.id} apiFetch={apiFetch} workspace={activeWorkspace} onBack={() => setScreen("project")} onMessagesChanged={(messages) => updateChatMessages(activeWorkspace.id, messages)} />}
          {screen === "intelligence" && <CodeIntelligenceScreen key={activeWorkspace.id} apiFetch={apiFetch} workspace={activeWorkspace} onBack={() => setScreen("project")} onReviewsChanged={(reviews) => updateCodeReviews(activeWorkspace.id, reviews)} />}
          {screen === "verification" && <VerificationScreen key={activeWorkspace.id} apiFetch={apiFetch} workspace={activeWorkspace} onBack={() => setScreen("project")} />}
          {screen === "history" && <HistoryScreen workspaces={workspaces} onOpen={openAnalysis} />}
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

function HistoryScreen({ workspaces, onOpen }: { workspaces: Workspace[]; onOpen: (workspace: Workspace, record: AnalysisRecord) => void }) {
  const entries = workspaces.flatMap((workspace) => (workspace.analyses || []).map((record) => ({ workspace, record })))
    .sort((left, right) => Date.parse(right.record.created_at) - Date.parse(left.record.created_at));
  return <div className="screen-stack">
    <div className="eyebrow"><span className="eyebrow-line" /> WORKSPACE HISTORY</div>
    <h1 className="screen-title">Analysis history</h1>
    <p className="screen-subtitle">Previous task plans saved with their repository workspace.</p>
    {entries.length ? <section className="workspace-list">{entries.map(({ workspace, record }) => <button className="workspace-row" key={`${workspace.id}-${record.id}`} onClick={() => onOpen(workspace, record)}>
      <span className="repo-avatar violet">✦</span><span className="repo-info"><strong>{workspace.name}</strong><small>{record.task}</small><small>{new Date(record.created_at).toLocaleString()}</small></span><span className="row-arrow">→</span>
    </button>)}</section> : <section className="panel-card"><h2>No saved analyses yet</h2><p className="overview-copy">Import a repository and analyze a task. Its result will appear here and remain available with that workspace.</p></section>}
  </div>;
}

function ProjectScreen({ workspace, task, onTaskChange, onAnalyze, onBack, onOpenChat, onRunReview, onRunVerification }: { workspace: Workspace; task: string; onTaskChange: (task: string) => void; onAnalyze: (event: FormEvent<HTMLFormElement>) => void; onBack: () => void; onOpenChat: () => void; onRunReview: () => void; onRunVerification: () => void }) {
  return <div className="screen-stack">
    <div className="screen-back"><button onClick={onBack}>← All workspaces</button><span className="demo-label">{workspace.projectOverview ? "IMPORTED REPOSITORY" : "SAMPLE PROJECT DATA"}</span></div>
    <div className="project-title-row"><div><div className="eyebrow"><span className="eyebrow-line" /> PROJECT OVERVIEW</div><h1 className="screen-title">{workspace.name}</h1><p className="screen-subtitle">{workspace.source} workspace · Repository analysis preview</p></div><div className="project-actions"><span className="ready-pill"><span className="tiny-dot green" /> Indexed</span><button className="secondary-button" onClick={onRunReview}>Review code <span>⌕</span></button><button className="secondary-button" onClick={onRunVerification}>Run checks <span>✓</span></button><button className="primary-button" onClick={onOpenChat}>Ask about this project <span>→</span></button></div></div>
    <div className="project-stats"><div><span>TECH STACK</span><strong>{workspace.projectOverview?.technologies.length ? workspace.projectOverview.technologies.slice(0, 3).join(" · ") : workspace.projectOverview ? "No framework detected" : "TypeScript · React · Node.js (sample)"}</strong></div><div><span>FILES DISCOVERED</span><strong>{workspace.fileCount ? `${workspace.fileCount} files` : "128 files (sample)"}</strong></div><div><span>SOURCE LANGUAGES</span><strong>{workspace.projectOverview?.languages.slice(0, 2).map((language) => language.name).join(" · ") || (workspace.projectOverview ? "Not detected" : "Sample project")}</strong></div></div>
    <div className="project-columns"><section className="panel-card"><div className="panel-heading"><div><h2>Repository structure</h2><p>{workspace.projectOverview ? "Top-level folders and files" : "Example structure · sample data"}</p></div><span className="panel-dots">···</span></div><div className="tree-list">{workspace.projectOverview ? workspace.projectOverview.top_level.slice(0, 10).map((item) => <div className="tree-row file-path-row" key={item.name}><span className={item.type === "folder" ? "folder-icon" : "file-icon"}>{item.type === "folder" ? "▰" : item.name.split(".").at(-1)?.slice(0, 2).toUpperCase() || "--"}</span><span title={item.name}>{item.name}{item.type === "folder" ? "/" : ""}</span></div>) : workspace.filesPreview ? workspace.filesPreview.slice(0, 6).map((file) => <div className="tree-row file-path-row" key={file}><span className="file-icon">{file.split(".").at(-1)?.slice(0, 2).toUpperCase() || "--"}</span><span title={file}>{file}</span></div>) : <><div className="tree-row"><span>⌄</span><span className="folder-icon">▰</span><strong>src</strong></div><div className="tree-row indent"><span>⌄</span><span className="folder-icon">▰</span><strong>checkout</strong></div><div className="tree-row indent-two"><span className="file-icon">TS</span><span>checkout.service.ts</span><small>148</small></div><div className="tree-row indent"><span>⌄</span><span className="folder-icon">▰</span><strong>products</strong></div><div className="tree-row indent-two"><span className="file-icon">TS</span><span>product.repository.ts</span><small>92</small></div><div className="tree-row"><span>⌄</span><span className="folder-icon">▰</span><strong>tests</strong></div><div className="tree-row indent"><span className="file-icon">TS</span><span>checkout.test.ts</span><small>126</small></div></>}</div><div className="file-preview-list">{workspace.projectOverview && <><div className="list-divider" /><strong className="list-caption">SAMPLE FILE PATHS</strong>{workspace.filesPreview?.slice(0, 5).map((file) => <div className="path-preview" key={file}><span>↳</span>{file}</div>)}</>}</div></section>
      <section className="panel-card overview-card"><div className="panel-heading"><div><h2>Detected technologies</h2><p>{workspace.projectOverview ? "Based on file extensions and manifests" : "Sample overview · placeholder"}</p></div><span className="sparkle">✦</span></div><p className="overview-copy">{workspace.projectOverview?.summary || (workspace.filesPreview ? "Project files imported. No supported language or framework markers were detected." : "A sample Node.js service organized around feature modules. This example shows the intended overview layout.")}</p>{workspace.projectOverview?.languages.length ? <div className="language-list">{workspace.projectOverview.languages.slice(0, 5).map((language) => <div key={language.name}><span>{language.name}</span><strong>{language.files} files</strong><span className="language-meter"><i style={{ width: `${Math.max(8, Math.round(language.files / workspace.projectOverview!.languages[0].files * 100))}%` }} /></span></div>)}</div> : !workspace.projectOverview && <div className="module-chips"><span>Authentication</span><span>Products</span><span>Checkout</span><span>Orders</span></div>}{workspace.projectOverview?.technologies.length ? <div className="module-chips">{workspace.projectOverview.technologies.slice(0, 8).map((technology) => <span key={technology}>{technology}</span>)}</div> : null}<div className="overview-note"><span>i</span> {workspace.projectOverview ? "This is deterministic manifest analysis; AI has not run." : "This sample summary is not based on an imported repository."}</div>{workspace.projectOverview?.manifest_files.length ? <div className="manifest-list"><strong>MANIFESTS</strong>{workspace.projectOverview.manifest_files.slice(0, 4).map((manifest) => <span key={manifest}>{manifest}</span>)}</div> : null}</section></div>
    {workspace.projectOverview && <section className="project-intelligence">
      <div className="intelligence-heading"><div><h2>Repository intelligence</h2><p>Deterministic signals inferred from repository paths and dependency manifests.</p></div><span className="demo-label">EVIDENCE BASED</span></div>
      <div className="intelligence-grid">
        <article className="intelligence-card"><h3>Architecture &amp; project map</h3><p className="intelligence-summary">{workspace.projectOverview.architecture.style}</p>{workspace.projectOverview.architecture.project_map.length ? workspace.projectOverview.architecture.project_map.map((layer) => <div className="intelligence-row" key={layer.name}><strong>{layer.name}</strong><span>{layer.file_count} files</span><small>{layer.evidence_files.slice(0, 2).join(" · ")}</small></div>) : <p className="intelligence-empty">No recognizable layers found in paths.</p>}</article>
        <article className="intelligence-card"><h3>API &amp; database</h3><div className="intelligence-pair"><strong>API frameworks</strong><span>{workspace.projectOverview.api.frameworks.join(", ") || "No framework signal found"}</span></div><div className="intelligence-evidence">{workspace.projectOverview.api.route_files.slice(0, 5).map((path) => <code key={path}>{path}</code>)}</div><div className="intelligence-pair"><strong>Database / ORM signals</strong><span>{workspace.projectOverview.database.systems.join(", ") || "No database signal found"}</span></div><div className="intelligence-evidence">{workspace.projectOverview.database.evidence_files.slice(0, 5).map((path) => <code key={path}>{path}</code>)}</div></article>
        <article className="intelligence-card"><h3>Authentication</h3><p className="intelligence-summary">{workspace.projectOverview.authentication.signals.join(", ") || "No authentication signal found"}</p>{workspace.projectOverview.authentication.evidence_files.length ? <div className="intelligence-evidence">{workspace.projectOverview.authentication.evidence_files.slice(0, 8).map((path) => <code key={path}>{path}</code>)}</div> : <p className="intelligence-empty">This is a static scan; absence of signals does not prove authentication is missing.</p>}</article>
      </div>
      <p className="intelligence-disclaimer">These are repository indicators, not a full architecture audit. Confirm each finding in its cited files.</p>
    </section>}
    <form className="task-box" onSubmit={onAnalyze}><div className="task-box-heading"><div className="task-icon">✦</div><div><h2>What task were you handed?</h2><p>Describe the request. Agent Lab will map it to the project and create a plan.</p></div></div><textarea value={task} onChange={(event) => onTaskChange(event.target.value)} placeholder="Example: Checkout sometimes times out when a cart contains many products. Find the bottleneck and suggest a safe fix." rows={3} /><div className="task-box-footer"><span>⌘ ↵ &nbsp; Analyze task</span><button className="primary-button" type="submit" disabled={!task.trim()}>Analyze task <span>→</span></button></div></form>
  </div>;
}

function AnalysisScreen({ apiFetch, workspace, analysisId, draftId, implementationStatus, onDraftCreated, onImplementationChanged, task, result, error, loading, approved, approvalLoading, approvalError, activeTab, onTab, onApprove, onBack }: { apiFetch: ApiFetch; workspace: Workspace; analysisId: string; draftId: string; implementationStatus: string; onDraftCreated: (id: string) => void; onImplementationChanged: (status: "applied" | "reverted") => void; task: string; result: TaskAnalysis | null; error: string; loading: boolean; approved: boolean; approvalLoading: boolean; approvalError: string; activeTab: "plan" | "files"; onTab: (tab: "plan" | "files") => void; onApprove: () => void; onBack: () => void }) {
  const [preview, setPreview] = useState<EvidencePreview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [draft, setDraft] = useState<ImplementationDraft | null>(null);
  const [draftLoading, setDraftLoading] = useState(false);
  const [draftError, setDraftError] = useState("");
  const [applyLoading, setApplyLoading] = useState(false);
  const [rollbackLoading, setRollbackLoading] = useState(false);
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

  useEffect(() => {
    if (!draftId || !/^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(workspace.id)) {
      setDraft(null);
      return;
    }
    let cancelled = false;
    setDraftLoading(true);
    apiFetch(`${apiBase}/api/workspaces/${workspace.id}/tasks/implementation-drafts/${draftId}`)
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "Could not load implementation draft.");
        if (!cancelled) setDraft(payload as ImplementationDraft);
      })
      .catch((cause) => { if (!cancelled) setDraftError(cause instanceof Error ? cause.message : "Could not load implementation draft."); })
      .finally(() => { if (!cancelled) setDraftLoading(false); });
    return () => { cancelled = true; };
  }, [apiBase, apiFetch, draftId, workspace.id]);

  async function generateDraft() {
    if (!approved || !analysisId || draftLoading) return;
    setDraftError("");
    setDraftLoading(true);
    try {
      const response = await apiFetch(`${apiBase}/api/workspaces/${workspace.id}/tasks/${analysisId}/implementation-draft`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Could not generate implementation draft.");
      const nextDraft = payload as ImplementationDraft;
      setDraft(nextDraft);
      onDraftCreated(nextDraft.id);
    } catch (cause) {
      setDraftError(cause instanceof Error ? cause.message : "Could not generate implementation draft.");
    } finally {
      setDraftLoading(false);
    }
  }

  async function applyDraft() {
    if (!draft || draft.status !== "pending_review" || applyLoading) return;
    const paths = draft.files.map((file) => `• ${file.path}`).join("\n");
    if (!window.confirm(`Apply these reviewed changes to the imported workspace?\n\n${paths}\n\nThis writes files in the Agent Lab workspace. It does not run code or commands.`)) return;
    setDraftError("");
    setApplyLoading(true);
    try {
      const response = await apiFetch(`${apiBase}/api/workspaces/${workspace.id}/tasks/implementation-drafts/${draft.id}/apply`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) {
        if (response.status === 409) setDraft({ ...draft, status: "stale" });
        throw new Error(payload.detail || "Could not apply implementation draft.");
      }
      setDraft({ ...draft, status: "applied" });
      onImplementationChanged("applied");
    } catch (cause) {
      setDraftError(cause instanceof Error ? cause.message : "Could not apply implementation draft.");
    } finally {
      setApplyLoading(false);
    }
  }

  async function rollbackDraft() {
    if (!draft || draft.status !== "applied" || rollbackLoading) return;
    const paths = draft.files.map((file) => `• ${file.path}`).join("\n");
    if (!window.confirm(`Revert these Agent Lab changes?\n\n${paths}\n\nIf a file changed after apply, rollback will be refused.`)) return;
    setDraftError("");
    setRollbackLoading(true);
    try {
      const response = await apiFetch(`${apiBase}/api/workspaces/${workspace.id}/tasks/implementation-drafts/${draft.id}/rollback`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Could not revert implementation changes.");
      setDraft({ ...draft, status: "reverted" });
      onImplementationChanged("reverted");
    } catch (cause) {
      setDraftError(cause instanceof Error ? cause.message : "Could not revert implementation changes.");
    } finally {
      setRollbackLoading(false);
    }
  }

  async function openEvidence(file: TaskAnalysis["relevant_files"][number]) {
    setPreview(null);
    setPreviewError("");
    setPreviewLoading(true);
    try {
      const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
      const response = await apiFetch(`${apiBase}/api/workspaces/${workspace.id}/files?path=${encodeURIComponent(file.path)}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Could not open this evidence file.");
      setPreview({ path: file.path, content: payload.content, startLine: file.start_line, endLine: file.end_line });
    } catch (previewRequestError) {
      setPreviewError(previewRequestError instanceof Error ? previewRequestError.message : "Could not open this evidence file.");
    } finally {
      setPreviewLoading(false);
    }
  }

  if (loading || error || result) return <div className="screen-stack">
    <div className="screen-back"><button onClick={onBack}>← {workspace.name}</button><span className="demo-label">{loading ? "ANALYZING REPOSITORY" : result ? "GEMINI ANALYSIS" : "ANALYSIS NEEDS ATTENTION"}</span></div>
    <div className="analysis-title-row"><div><div className="eyebrow"><span className="eyebrow-line" /> TASK ANALYSIS</div><h1 className="screen-title">Task understanding</h1><p className="screen-subtitle">Repository evidence and a proposed implementation plan.</p></div>{result && <span className="confidence-pill">◈ &nbsp; Evidence linked</span>}</div>
    <div className="task-summary"><div className="user-avatar">JD</div><div><span className="summary-label">HANDOFF</span><p>{task}</p></div><button onClick={onBack}>Edit</button></div>
    {loading && <section className="panel-card"><h2>Reading repository context…</h2><p className="overview-copy">Selecting relevant source files and asking Gemini to prepare an evidence-linked plan. No repository code is executed.</p></section>}
    {error && <section className="panel-card"><h2>Could not analyze this task</h2><p className="form-error" role="alert">{error}</p><p className="overview-copy">For imported workspaces, configure GEMINI_API_KEY in <code>apps/api/.env</code>, then restart the API.</p></section>}
    {approvalError && <p className="form-error" role="alert">{approvalError}</p>}
    {result && <div className="analysis-grid"><div className="analysis-main">
      <section className="panel-card understanding-card"><div className="panel-heading"><div><h2><span className="sparkle">✦</span> What I understand</h2><p>Generated from the imported repository</p></div><span className="ai-badge">GEMINI</span></div><p className="understanding-copy">{result.task_summary}</p>
        {result.assumptions.map((item, i) => <div className="assumption-box" key={`a-${i}`}><span>i</span><div><strong>Assumption</strong><p>{item}</p></div></div>)}
        {result.questions.map((item, i) => <div className="assumption-box" key={`q-${i}`}><span>?</span><div><strong>Open question</strong><p>{item}</p></div></div>)}
      </section>
      <section className="panel-card plan-card"><div className="panel-heading plan-heading"><div><h2>Investigation &amp; plan</h2><p>Review the suggested steps before implementation begins.</p></div>{approved && <span className="approved-pill">✓ Plan approved</span>}</div><div className="plan-steps">{result.plan.map((step, index) => <div className="plan-step" key={`${step.title}-${index}`}><span className="step-number">{String(index + 1).padStart(2, "0")}</span><div><strong>{step.title}</strong><p>{step.detail}</p><span className="step-tag">Verify: {step.verification}</span></div></div>)}</div>
        {!!result.verification.length && <div className="evidence-list"><strong>Suggested verification</strong>{result.verification.map((item, index) => <p key={index}>{item}</p>)}</div>}
        <div className="plan-footer"><span>Relevant files <strong>{result.relevant_files.length}</strong></span><button className={approved ? "secondary-button" : "primary-button"} onClick={onApprove} disabled={approved || approvalLoading}>{approvalLoading ? "Saving approval…" : approved ? "Plan approved" : "Approve plan"} <span>{approved ? "✓" : "→"}</span></button></div>
      </section>
      {approved && <section className="panel-card implementation-card"><div className="panel-heading"><div><h2>Controlled implementation</h2><p>Generate a reviewable patch from this approved plan.</p></div><span className="demo-label">NO COMMANDS RUN</span></div><p className="overview-copy">Agent Lab will prepare up to five file changes. Review every diff below; files are written only after you choose Apply changes.</p>{workspace.implementationBranch && <div className="implementation-branch"><span>ISOLATED GIT BRANCH</span><code>{workspace.implementationBranch}</code></div>}
        {(!draft || draft.status === "stale" || draft.status === "reverted") && <button className="primary-button" onClick={() => void generateDraft()} disabled={draftLoading || !analysisId}>{draftLoading ? "Preparing draft…" : draft?.status === "stale" ? "Regenerate stale draft" : draft?.status === "reverted" ? "Generate a new draft" : "Generate implementation draft"}<span>→</span></button>}
        {draftLoading && (!draft || draft.status === "stale" || draft.status === "reverted") && <p className="draft-note">Reading approved plan and relevant source files…</p>}
        {draftError && <p className="form-error" role="alert">{draftError}</p>}
        {draft && <><p className="implementation-summary">{draft.summary}</p><div className="draft-file-list">{draft.files.map((file) => <article className="draft-file" key={file.path}><div className="draft-file-heading"><strong>{file.path}</strong><span>{file.action === "create" ? "NEW FILE" : "UPDATE"}</span></div><p>{file.rationale}</p><pre>{file.diff || "(No textual diff)"}</pre></article>)}</div><div className="implementation-footer"><span>{draft.status === "applied" || implementationStatus === "applied" ? "Changes applied to workspace" : draft.status === "reverted" || implementationStatus === "reverted" ? "Changes reverted" : draft.status === "stale" ? "Source files changed; regenerate this draft before applying" : `${draft.files.length} file change${draft.files.length === 1 ? "" : "s"} · review before applying`}</span>{draft.status === "pending_review" && implementationStatus !== "applied" && <button className="primary-button" onClick={() => void applyDraft()} disabled={applyLoading}>{applyLoading ? "Applying…" : "Apply changes"}<span>✓</span></button>}{draft.status === "applied" && implementationStatus === "applied" && <button className="secondary-button" onClick={() => void rollbackDraft()} disabled={rollbackLoading}>{rollbackLoading ? "Reverting…" : "Revert changes"}<span>↶</span></button>}</div></>}
        {draftLoading && draft && <p className="draft-note">Refreshing saved draft…</p>}
      </section>}
    </div><aside className="analysis-aside"><section className="panel-card evidence-card"><div className="panel-heading"><div><h2>Code evidence</h2><p>Click a reference to inspect its source lines</p></div><span className="evidence-count">{result.relevant_files.length}</span></div>{result.relevant_files.map((file) => <button className="evidence-file compact evidence-trigger" key={file.path} type="button" onClick={() => openEvidence(file)}><span className="file-icon">{file.path.split(".").at(-1)?.slice(0, 2).toUpperCase() || "--"}</span><span className="evidence-trigger-copy"><strong>{file.path}</strong><small>{file.reason}</small><span className="line-ref">L{file.start_line}–{file.end_line} · view source</span></span></button>)}{!result.relevant_files.length && <p className="overview-copy">The model did not return verifiable file citations for this task.</p>}</section><div className="disclaimer"><span>i</span><p>AI suggestions need developer review. Repository contents are treated as untrusted text; code was not executed.</p></div></aside></div>}
    {previewLoading && <div className="modal-backdrop"><section className="evidence-modal"><p className="overview-copy">Opening referenced source…</p></section></div>}
    {previewError && <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) setPreviewError(""); }}><section className="evidence-modal"><button className="modal-close" onClick={() => setPreviewError("")} aria-label="Close">×</button><h2>Could not open source</h2><p className="form-error" role="alert">{previewError}</p></section></div>}
    {preview && <EvidencePreviewModal preview={preview} onClose={() => setPreview(null)} />}
  </div>;
  return <div className="screen-stack">
    <div className="screen-back"><button onClick={onBack}>← {workspace.name}</button><span className="demo-label">SAMPLE AI RESULT</span></div>
    <div className="analysis-title-row"><div><div className="eyebrow"><span className="eyebrow-line" /> TASK ANALYSIS</div><h1 className="screen-title">Task understanding</h1><p className="screen-subtitle">Repository evidence and a proposed implementation plan.</p></div><span className="confidence-pill">◈ &nbsp; Evidence linked</span></div>
    <div className="task-summary"><div className="user-avatar">JD</div><div><span className="summary-label">HANDOFF</span><p>{task}</p></div><button aria-label="Edit task" onClick={onBack}>Edit</button></div>
    <div className="analysis-grid"><div className="analysis-main"><section className="panel-card understanding-card"><div className="panel-heading"><div><h2><span className="sparkle">✦</span> What I understand</h2><p>Summary based on sample repository context</p></div><span className="ai-badge">AI SUMMARY</span></div><p className="understanding-copy">The checkout flow may be doing repeated product lookups while processing a cart. The first thing to verify is whether each cart item triggers its own database request, which could increase response time as carts grow.</p><div className="assumption-box"><span>!</span><div><strong>Open question</strong><p>Should checkout fail when a product is missing, or continue with the remaining items?</p></div></div></section>
      <section className="panel-card plan-card"><div className="panel-heading plan-heading"><div><h2>Investigation & plan</h2><p>Review the suggested steps before implementation.</p></div>{approved && <span className="approved-pill">✓ Plan approved</span>}</div><div className="tab-bar"><button className={activeTab === "plan" ? "selected" : ""} onClick={() => onTab("plan")}>Plan <span>3</span></button><button className={activeTab === "files" ? "selected" : ""} onClick={() => onTab("files")}>Relevant files <span>3</span></button></div>
        {activeTab === "plan" ? <div className="plan-steps">{planSteps.map((step, index) => <div className="plan-step" key={step.title}><span className="step-number">0{index + 1}</span><div><strong>{step.title}</strong><p>{step.detail}</p><span className="step-tag">{step.tag}</span></div></div>)}</div> : <div className="evidence-list">{sampleFiles.map((file) => <div className="evidence-file" key={file.name}><span className="file-icon">{file.kind}</span><div><strong>{file.name}</strong><p>{file.reason}</p></div><span className="line-ref">L{file.lines}</span></div>)}</div>}
        <div className="plan-footer"><span>Estimated scope <strong>2–3 files</strong></span><button className={approved ? "secondary-button" : "primary-button"} onClick={onApprove} disabled={approved || approvalLoading}>{approvalLoading ? "Saving approval…" : approved ? "Plan approved" : "Approve plan"} <span>{approved ? "✓" : "→"}</span></button></div>
      </section></div>
      <aside className="analysis-aside"><section className="panel-card evidence-card"><div className="panel-heading"><div><h2>Code evidence</h2><p>Files that support this analysis</p></div><span className="evidence-count">3</span></div>{sampleFiles.map((file) => <div className="evidence-file compact" key={file.name}><span className="file-icon">{file.kind}</span><div><strong>{file.name}</strong><small>{file.reason}</small></div></div>)}<button className="text-button" onClick={() => onTab("files")}>View all relevant files <span>→</span></button></section><section className="panel-card verify-card"><div className="verify-icon">✓</div><div><h3>Suggested verification</h3><p>Run checkout tests and compare query counts for a multi-item cart.</p></div></section><div className="disclaimer"><span>i</span><p>Frontend preview only. These findings are sample content, not an analysis of the selected repository.</p></div></aside></div>
  </div>;
}

type VerificationOptions = { manager: string; sandbox_available: boolean; note: string; checks: Array<{ id: string; label: string }> };
type VerificationResult = { check_id: string; label: string; status: "passed" | "failed"; exit_code: number; output: string };

function VerificationScreen({ apiFetch, workspace, onBack }: { apiFetch: ApiFetch; workspace: Workspace; onBack: () => void }) {
  const [options, setOptions] = useState<VerificationOptions | null>(null);
  const [result, setResult] = useState<VerificationResult | null>(null);
  const [error, setError] = useState("");
  const [loadingId, setLoadingId] = useState("");
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

  useEffect(() => {
    let cancelled = false;
    apiFetch(`${apiBase}/api/workspaces/${workspace.id}/verification`)
      .then(async (response) => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || "Could not inspect verification options.");
        if (!cancelled) setOptions(payload as VerificationOptions);
      })
      .catch((cause) => { if (!cancelled) setError(cause instanceof Error ? cause.message : "Could not inspect verification options."); });
    return () => { cancelled = true; };
  }, [apiBase, apiFetch, workspace.id]);

  async function runCheck(check: VerificationOptions["checks"][number]) {
    if (!window.confirm(`Run “${check.label}” in a disposable Docker container?\n\nSource is mounted read-only, network is disabled, and the container is resource-limited.`)) return;
    setLoadingId(check.id);
    setError("");
    setResult(null);
    try {
      const response = await apiFetch(`${apiBase}/api/workspaces/${workspace.id}/verification`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ check_id: check.id }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Verification did not complete.");
      setResult(payload as VerificationResult);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Verification did not complete.");
    } finally {
      setLoadingId("");
    }
  }

  return <>
    <div className="screen-back"><button onClick={onBack}>← {workspace.name}</button><span className="demo-label">CONTROLLED VERIFICATION</span></div>
    <div className="intelligence-title-row"><div><div className="eyebrow"><span className="eyebrow-line" /> TESTING ENGINE</div><h1 className="screen-title">Verify {workspace.name}</h1><p className="screen-subtitle">Only manifest-declared checks from a fixed allowlist are offered.</p></div></div>
    <section className="panel-card"><div className="panel-heading"><div><h2>Available checks</h2><p>Detected project type: {options?.manager || "scanning…"}</p></div><span className="demo-label">DOCKER SANDBOX</span></div>
      <p className="overview-copy">{options?.note || "Inspecting repository manifests…"}</p>
      {options && !options.sandbox_available && <p className="form-error" role="status">Docker is not available to the API host. Checks are disabled; repository commands will not run.</p>}
      {error && <p className="form-error" role="alert">{error}</p>}
      {options?.checks.length ? <div className="project-actions">{options.checks.map((check) => <button className="secondary-button" key={check.id} onClick={() => void runCheck(check)} disabled={!options.sandbox_available || Boolean(loadingId)}>{loadingId === check.id ? "Running…" : check.label}<span>▶</span></button>)}</div> : options && <p className="intelligence-empty">No supported checks were detected from this repository’s manifests.</p>}
      {result && <div className="overview-note"><span>{result.status === "passed" ? "✓" : "!"}</span> {result.label}: {result.status} (exit {result.exit_code})</div>}
      {result && <pre className="verification-output">{result.output}</pre>}
      <p className="overview-copy">Dependencies are not installed automatically. A check that needs missing packages will report that in its output.</p>
    </section>
  </>;
}

function CodeIntelligenceScreen({ apiFetch, workspace, onBack, onReviewsChanged }: { apiFetch: ApiFetch; workspace: Workspace; onBack: () => void; onReviewsChanged: (reviews: CodeReviewRecord[]) => void }) {
  const [reviews, setReviews] = useState<CodeReviewRecord[]>(workspace.codeReviews || []);
  const [selectedId, setSelectedId] = useState(reviews[0]?.id || "");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<EvidencePreview | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
  const workspaceReady = /^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(workspace.id);
  const selected = reviews.find((item) => item.id === selectedId) || reviews[0];

  async function runReview() {
    if (!workspaceReady || loading) return;
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch(`${apiBase}/api/workspaces/${workspace.id}/code-intelligence/review`, { method: "POST" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Could not review this repository.");
      const next = [payload as CodeReviewRecord, ...reviews.filter((item) => item.id !== payload.id)].slice(0, 20);
      setReviews(next);
      setSelectedId(payload.id);
      onReviewsChanged(next);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not reach the Agent Lab API.");
    } finally {
      setLoading(false);
    }
  }

  async function openFinding(finding: CodeReview["findings"][number]) {
    setPreviewError("");
    setPreviewLoading(true);
    try {
      const response = await apiFetch(`${apiBase}/api/workspaces/${workspace.id}/files?path=${encodeURIComponent(finding.file)}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Could not load this source file.");
      setPreview({ path: payload.path, content: payload.content, startLine: finding.start_line, endLine: finding.end_line });
    } catch (cause) {
      setPreviewError(cause instanceof Error ? cause.message : "Could not load this source file.");
    } finally {
      setPreviewLoading(false);
    }
  }

  return <div className="screen-stack intelligence-screen">
    <div className="screen-back"><button onClick={onBack}>← Project overview</button><span className="demo-label">READ ONLY REVIEW</span></div>
    <div className="intelligence-title-row"><div><div className="eyebrow"><span className="eyebrow-line" /> CODE INTELLIGENCE</div><h1 className="screen-title">Review {workspace.name}</h1><p className="screen-subtitle">Inspect likely bugs, security, performance, refactoring, quality, and dependency risks.</p></div><button className="primary-button" onClick={() => void runReview()} disabled={loading || !workspaceReady}>{loading ? "Reviewing repository…" : "Run code review"}<span>⌕</span></button></div>
    {!workspaceReady && <div className="chat-error">Import a repository to run code intelligence.</div>}
    {error && <div className="chat-error" role="alert">{error}</div>}
    {previewError && <div className="chat-error" role="alert">{previewError}</div>}{previewLoading && <p className="chat-pending" role="status">Loading source evidence…</p>}
    {selected ? <>
      <section className="panel-card review-summary"><div className="panel-heading"><div><h2>Review summary</h2><p>{new Date(selected.created_at).toLocaleString()}</p></div><span className="ai-badge">GEMINI</span></div><p className="overview-copy">{selected.result.review_summary}</p><p className="review-scope">{selected.result.scope_note}</p>
        {!!selected.result.files_reviewed.length && <div className="reviewed-files"><strong>FILES REVIEWED</strong>{selected.result.files_reviewed.map((file) => <code key={file}>{file}</code>)}</div>}
      </section>
      <section className="review-dependencies"><div><h2>Dependency review</h2><p>Manifest-level review only; no live CVE database or registry version check was run.</p></div>{selected.result.dependency_notes.length ? <ul>{selected.result.dependency_notes.map((note, index) => <li key={index}>{note}</li>)}</ul> : <p className="intelligence-empty">No dependency-specific note was returned for the supplied manifests.</p>}</section>
      <div className="review-findings-heading"><div><h2>Findings</h2><p>{selected.result.findings.length} evidence-linked items</p></div>{reviews.length > 1 && <label>Review run <select value={selected.id} onChange={(event) => setSelectedId(event.target.value)}>{reviews.map((review) => <option key={review.id} value={review.id}>{new Date(review.created_at).toLocaleString()}</option>)}</select></label>}</div>
      {selected.result.findings.length ? <div className="review-findings">{selected.result.findings.map((finding, index) => <article className="finding-card" key={`${selected.id}-${index}`}><div className="finding-topline"><span className={`severity-pill severity-${finding.severity}`}>{finding.severity}</span><span className="finding-category">{finding.category}</span></div><h3>{finding.title}</h3><p>{finding.description}</p><div className="finding-recommendation"><strong>Suggested next step</strong><span>{finding.recommendation}</span></div><button className="finding-source" onClick={() => void openFinding(finding)}><span>{finding.file}</span><small>L{finding.start_line}–{finding.end_line} ↗</small></button></article>)}</div> : <section className="panel-card"><h2>No evidence-linked findings</h2><p className="overview-copy">The review did not return a supported issue in the sampled files. This is not a guarantee that the entire repository is free of defects.</p></section>}
    </> : <section className="panel-card"><h2>Run a read-only review</h2><p className="overview-copy">Agent Lab will inspect a bounded set of source files and dependency manifests, then return findings that point back to their source.</p></section>}
    {preview && <EvidencePreviewModal preview={preview} onClose={() => setPreview(null)} />}
  </div>;
}

function ChatScreen({ apiFetch, workspace, onBack, onMessagesChanged }: { apiFetch: ApiFetch; workspace: Workspace; onBack: () => void; onMessagesChanged: (messages: ChatTurn[]) => void }) {
  const [messages, setMessages] = useState<ChatTurn[]>(workspace.chatMessages || []);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [preview, setPreview] = useState<EvidencePreview | null>(null);
  const [previewError, setPreviewError] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";
  const workspaceReady = /^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(workspace.id);

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const question = draft.trim();
    if (!question || sending) return;
    if (!workspaceReady) {
      setError("Import a repository before starting a project-specific chat.");
      return;
    }
    setSending(true);
    setError("");
    try {
      const response = await apiFetch(`${apiBase}/api/workspaces/${workspace.id}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: question }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Could not answer this project question.");
      const next = [...messages, payload as ChatTurn].slice(-50);
      setMessages(next);
      onMessagesChanged(next);
      setDraft("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not reach the Agent Lab API.");
    } finally {
      setSending(false);
    }
  }

  async function openEvidence(file: ChatTurn["relevant_files"][number]) {
    setPreviewError("");
    setPreviewLoading(true);
    try {
      const response = await apiFetch(`${apiBase}/api/workspaces/${workspace.id}/files?path=${encodeURIComponent(file.path)}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Could not load this source file.");
      setPreview({ path: payload.path, content: payload.content, startLine: file.start_line, endLine: file.end_line });
    } catch (cause) {
      setPreviewError(cause instanceof Error ? cause.message : "Could not load this source file.");
    } finally {
      setPreviewLoading(false);
    }
  }

  return <div className="screen-stack chat-screen">
    <div className="screen-back"><button onClick={onBack}>← Project overview</button><span className="demo-label">REPOSITORY CONTEXT</span></div>
    <div><div className="eyebrow"><span className="eyebrow-line" /> AI DEVELOPER CHAT</div><h1 className="screen-title">Ask about {workspace.name}</h1><p className="screen-subtitle">Answers use this repository&apos;s source snippets and include file evidence where available.</p></div>
    <section className="chat-transcript" aria-live="polite">
      {!messages.length && <div className="chat-empty"><span className="chat-empty-icon">✧</span><h2>Understand this codebase</h2><p>Ask about architecture, a function, where a feature lives, or how data moves through the project.</p><div className="chat-suggestions">{["Explain the architecture of this project", "Where is authentication implemented?", "Trace the data flow for the main API request"].map((suggestion) => <button key={suggestion} onClick={() => setDraft(suggestion)}>{suggestion}<span>↗</span></button>)}</div></div>}
      {messages.map((turn) => <article className="chat-turn" key={turn.id}>
        <div className="chat-question"><span className="chat-role user-role">YOU</span><p>{turn.question}</p></div>
        <div className="chat-answer"><span className="chat-role agent-role">AGENT LAB</span><p>{turn.answer}</p>
          {turn.relevant_files.length > 0 && <div className="chat-citations"><strong>Repository evidence</strong>{turn.relevant_files.map((file) => <button key={`${turn.id}-${file.path}`} onClick={() => void openEvidence(file)}><span>{file.path}</span><small>L{file.start_line}–{file.end_line}</small></button>)}</div>}
        </div>
      </article>)}
      {sending && <div className="chat-pending" role="status"><span className="status-dot" /> Reading repository context and preparing an answer…</div>}
    </section>
    {previewError && <p className="form-error" role="alert">{previewError}</p>}{previewLoading && <p className="chat-pending" role="status">Loading source file…</p>}
    {error && <div className="chat-error" role="alert">{error}</div>}
    <form className="chat-composer" onSubmit={sendMessage}><label htmlFor="project-chat-input" className="sr-only">Ask a question about this repository</label><textarea id="project-chat-input" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Ask about a file, function, feature, or data flow…" rows={3} maxLength={2000} disabled={sending} /><div className="chat-composer-footer"><span>{workspaceReady ? "Repository code is read as text and never executed." : "Import a repository to enable project chat."}</span><button type="submit" className="primary-button" disabled={sending || !draft.trim() || !workspaceReady}>{sending ? "Thinking…" : "Ask Agent Lab"}<span>↑</span></button></div></form>
    {preview && <EvidencePreviewModal preview={preview} onClose={() => setPreview(null)} />}
  </div>;
}

function EvidencePreviewModal({ preview, onClose }: { preview: EvidencePreview; onClose: () => void }) {
  const lines = preview.content.split(/\r?\n/);
  const firstLine = Math.max(1, preview.startLine - 8);
  const lastLine = Math.min(lines.length, preview.endLine + 8);
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="evidence-modal" role="dialog" aria-modal="true" aria-labelledby="evidence-title">
      <button className="modal-close" onClick={onClose} aria-label="Close source preview">×</button>
      <div className="eyebrow"><span className="eyebrow-line" /> SOURCE EVIDENCE</div>
      <h2 id="evidence-title">{preview.path}</h2>
      <p className="screen-subtitle">Lines {preview.startLine}–{preview.endLine} are highlighted. This preview is read-only.</p>
      <pre className="code-view evidence-code">{lines.slice(firstLine - 1, lastLine).map((line, index) => {
        const lineNumber = firstLine + index;
        const highlighted = lineNumber >= preview.startLine && lineNumber <= preview.endLine;
        return <span className={`code-line ${highlighted ? "evidence-line-highlight" : ""}`} key={lineNumber}><span className="line-number">{lineNumber}</span><span>{line || " "}</span></span>;
      })}</pre>
      <div className="evidence-modal-footer"><span>Repository code is displayed as text and is never executed.</span><button className="secondary-button editor-action" onClick={onClose}>Close</button></div>
    </section>
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
