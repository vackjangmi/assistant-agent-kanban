from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..config import PROJECT_ROOT


def build_ui_router() -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> str:
        runtime = request.app.state.runtime
        default_target_repo_value = str((runtime.config.repo_discovery.root or PROJECT_ROOT.parent).expanduser().resolve())
        default_target_repo = json.dumps(default_target_repo_value)
        default_base_branch = json.dumps(runtime.config.base_branch)
        return f"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FS Kanban Agent</title>
  <link rel="stylesheet" href="https://uicdn.toast.com/editor/latest/toastui-editor.min.css">
  <style>
    :root {{ --bg-top: #f7f2e8; --bg-bottom: #e8eef5; --panel: rgba(255,255,255,0.78); --panel-strong: rgba(255,252,247,0.95); --border: rgba(24,32,38,0.15); --accent: #7c4f2c; --accent-strong: #5f3417; --accent-soft: rgba(124,79,44,0.12); --success: #217349; --danger: #a33a2a; --text: #182026; --muted: #53616c; --shadow: 0 18px 40px rgba(0,0,0,0.12); }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: Georgia, serif; margin: 0; background: linear-gradient(180deg, var(--bg-top), var(--bg-bottom)); color: var(--text); }}
    body.modal-open {{ overflow: hidden; }}
    header {{ padding: 16px 20px; display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }}
    .header-actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    button {{ padding: 9px 14px; border: 1px solid var(--text); background: #fff9ef; cursor: pointer; font: inherit; }}
    button.primary {{ background: var(--accent); border-color: var(--accent-strong); color: #fff; }}
    .ghost-button {{ background: transparent; border-color: var(--border); }}
    button:disabled {{ opacity: 0.7; cursor: progress; }}
    #board {{ display: grid; grid-template-columns: repeat(5, minmax(220px, 1fr)); gap: 12px; padding: 0 20px 20px; }}
    .column {{ background: var(--panel); border: 1px solid var(--border); padding: 12px; min-height: 160px; }}
    .column h2 {{ margin-top: 0; }}
    .card {{ background: white; border-left: 4px solid var(--accent); padding: 10px; margin: 10px 0; box-shadow: 0 4px 10px rgba(0,0,0,0.05); }}
    .card-button {{ width: 100%; text-align: left; border: 0; background: transparent; padding: 0; cursor: pointer; color: inherit; }}
    .card:hover {{ transform: translateY(-1px); box-shadow: 0 8px 20px rgba(0,0,0,0.08); }}
    .card-meta {{ color: var(--muted); font-size: 0.95rem; }}
    .card-meta.running {{ color: var(--accent-strong); font-variant-numeric: tabular-nums; }}
    .card-model {{ margin-top: 8px; padding: 7px 8px; border: 1px solid var(--border); background: var(--accent-soft); color: var(--text); font-size: 0.86rem; line-height: 1.35; }}
    .card-model strong {{ display: block; font-size: 0.78rem; letter-spacing: 0.04em; text-transform: uppercase; color: var(--accent-strong); }}
    .modal {{ position: fixed; inset: 0; display: flex; align-items: center; justify-content: center; padding: 24px; background: rgba(24,32,38,0.36); backdrop-filter: blur(4px); }}
    .modal[hidden] {{ display: none; }}
    .modal-panel {{ width: min(1040px, 100%); max-height: calc(100vh - 48px); overflow-y: auto; overflow-x: hidden; background: rgba(255,255,255,0.95); border: 1px solid var(--border); box-shadow: var(--shadow); padding: 22px; }}
    .modal-head {{ display: flex; justify-content: space-between; align-items: start; gap: 16px; margin-bottom: 14px; }}
    .modal-copy p {{ margin: 6px 0 0; color: var(--muted); }}
    .composer-grid {{ display: grid; grid-template-columns: repeat(2, minmax(280px, 1fr)); gap: 16px; }}
    .group {{ display: grid; gap: 12px; align-content: start; }}
    .field {{ display: grid; gap: 6px; }}
    .field label {{ font-weight: 700; }}
    .field span {{ color: var(--muted); font-size: 0.95rem; }}
    .field input, .field textarea {{ width: 100%; border: 1px solid var(--border); background: rgba(255,255,255,0.98); padding: 10px 12px; font: inherit; color: var(--text); }}
    .field textarea {{ min-height: 96px; resize: vertical; }}
    .field.compact textarea {{ min-height: 72px; }}
    .error-text {{ min-height: 1.1em; color: var(--danger); font-size: 0.9rem; }}
    .form-error {{ margin-bottom: 12px; padding: 10px 12px; border: 1px solid rgba(163,58,42,0.3); background: rgba(163,58,42,0.08); color: var(--danger); }}
    .form-error[hidden] {{ display: none; }}
    .form-success {{ margin: 0 20px 20px; padding: 10px 12px; border: 1px solid rgba(33,115,73,0.25); background: rgba(33,115,73,0.09); color: #217349; }}
    .form-success[hidden] {{ display: none; }}
    .settings-shell {{ display: grid; gap: 18px; }}
    .settings-copy {{ padding: 16px 18px; border: 1px solid var(--border); background: linear-gradient(135deg, rgba(255,249,239,0.96), rgba(247,242,232,0.82)); }}
    .settings-copy p {{ margin: 8px 0 0; color: var(--muted); }}
    .settings-toolbar {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; flex-wrap: wrap; }}
    .settings-toolbar p {{ margin: 0; color: var(--muted); }}
    .settings-grid {{ display: grid; grid-template-columns: repeat(2, minmax(240px, 1fr)); gap: 14px; }}
    .settings-card {{ display: grid; gap: 6px; padding: 16px; border: 1px solid var(--border); background: var(--panel-strong); box-shadow: 0 10px 22px rgba(24,32,38,0.06); }}
    .settings-card strong {{ font-size: 1rem; }}
    .settings-card span {{ color: var(--muted); font-size: 0.95rem; }}
    .settings-card input {{ width: 100%; border: 1px solid var(--border); background: rgba(255,255,255,0.98); padding: 10px 12px; font: inherit; color: var(--text); }}
    .settings-card small {{ color: var(--muted); font-size: 0.88rem; }}
    .settings-path {{ padding: 12px 14px; border: 1px solid var(--border); background: var(--accent-soft); color: var(--muted); }}
    .settings-status {{ padding: 10px 12px; border: 1px solid var(--border); background: rgba(255,255,255,0.85); color: var(--muted); }}
    .settings-status[data-tone="success"] {{ border-color: rgba(33,115,73,0.25); background: rgba(33,115,73,0.09); color: var(--success); }}
    .settings-status[data-tone="error"] {{ border-color: rgba(163,58,42,0.3); background: rgba(163,58,42,0.08); color: var(--danger); }}
    .form-actions {{ display: flex; justify-content: flex-end; gap: 10px; margin-top: 16px; }}
    .task-meta-grid {{ display: grid; grid-template-columns: repeat(2, minmax(220px, 1fr)); gap: 10px 16px; margin-bottom: 18px; }}
    .meta-item span {{ display: block; color: var(--muted); font-size: 0.9rem; }}
    .meta-item strong {{ display: block; margin-top: 2px; }}
    .task-model-grid {{ display: grid; gap: 10px; }}
    .task-model-row {{ display: grid; grid-template-columns: minmax(0, 160px) minmax(0, 1fr); gap: 10px 14px; align-items: start; padding: 10px 12px; border: 1px solid var(--border); background: rgba(124,79,44,0.06); }}
    .task-model-row span {{ color: var(--accent-strong); font-size: 0.84rem; letter-spacing: 0.04em; text-transform: uppercase; }}
    .task-model-row strong {{ display: block; font-size: 0.98rem; overflow-wrap: anywhere; }}
    .task-model-row small {{ display: block; margin-top: 3px; color: var(--muted); font-size: 0.86rem; }}
    .task-tabs {{ display: flex; gap: 8px; margin-bottom: 14px; }}
    .task-tabs button.active {{ background: var(--accent); color: #fff; border-color: var(--accent-strong); }}
    .task-panel[hidden] {{ display: none; }}
    .task-section {{ margin-bottom: 16px; }}
    .task-section h3 {{ margin-bottom: 8px; }}
    .task-list {{ margin: 0; padding-left: 18px; color: var(--muted); }}
    .verification-actions {{ border: 1px solid var(--border); background: rgba(255,249,239,0.9); padding: 14px; margin-bottom: 14px; }}
    .verification-actions[hidden] {{ display: none; }}
    .verification-actions textarea {{ width: 100%; min-height: 96px; resize: vertical; border: 1px solid var(--border); background: rgba(255,255,255,0.98); padding: 10px 12px; font: inherit; color: var(--text); }}
    .log-layout {{ display: grid; grid-template-columns: minmax(0, 240px) minmax(0, 1fr); gap: 14px; }}
    .log-file-list {{ display: grid; gap: 8px; align-content: start; }}
    .log-file-list button {{ text-align: left; }}
    .log-file-list button.active {{ background: var(--accent); color: #fff; border-color: var(--accent-strong); }}
    .log-viewer {{ min-height: 320px; max-height: 50vh; overflow: auto; border: 1px solid var(--border); background: rgba(248,246,240,0.95); padding: 14px; white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 1rem; line-height: 1.55; }}
    .muted {{ color: var(--muted); }}
    .editor-toolbar {{ display: flex; justify-content: space-between; gap: 10px; margin-bottom: 10px; align-items: center; flex-wrap: wrap; }}
    .artifact-layout {{ display: grid; grid-template-columns: minmax(0, 240px) minmax(0, 1fr); gap: 14px; }}
    .artifact-stage {{ min-width: 0; }}
    .editor-textarea {{ width: 100%; min-height: 360px; resize: vertical; border: 1px solid var(--border); background: rgba(255,255,255,0.98); padding: 12px; font: inherit; }}
    .editor-host {{ min-height: 420px; border: 1px solid var(--border); background: #fff; }}
    .viewer-host {{ min-height: 420px; border: 1px solid var(--border); background: #fff; overflow: auto; padding: 18px; }}
    .editor-host[hidden] {{ display: none; }}
    .viewer-host[hidden] {{ display: none; }}
    .mode-pill {{ display: inline-flex; align-items: center; padding: 4px 10px; border: 1px solid var(--border); background: #f7efe1; color: var(--accent-strong); font-size: 0.9rem; margin-right: 8px; }}
    .toastui-editor-defaultUI, .toastui-editor-main, .toastui-editor-md-container, .toastui-editor-ww-container, .toastui-editor-contents {{ max-width: 100%; min-width: 0; }}
    .viewer-host .toastui-editor-contents {{ overflow-wrap: anywhere; word-break: break-word; }}
    @media (max-width: 900px) {{ #board, .composer-grid, .task-meta-grid, .log-layout, .artifact-layout, .settings-grid, .task-model-row {{ grid-template-columns: 1fr; }} .modal {{ padding: 12px; align-items: stretch; }} .modal-panel {{ max-height: none; }} .form-actions {{ flex-direction: column-reverse; }} .form-actions button {{ width: 100%; }} }}
  </style>
</head>
<body>
  <header>
    <h1>Filesystem Kanban Agent</h1>
    <div class="header-actions">
      <button id="open-composer" class="primary">New request</button>
      <button id="open-settings" class="ghost-button">Model settings</button>
      <button id="refresh">Refresh</button>
    </div>
  </header>
  <div id="form-success" class="form-success" hidden></div>
  <section id="request-modal" class="modal" hidden aria-hidden="true">
    <div class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="request-modal-title">
      <div class="modal-head">
        <div class="modal-copy">
          <h2 id="request-modal-title">Create request</h2>
          <p>Build a structured <code>REQUEST.md</code> with repo-aware defaults and editable planning fields.</p>
        </div>
        <button type="button" id="close-composer" aria-label="Close request form">Close</button>
      </div>
      <div id="form-error" class="form-error" hidden></div>
      <form id="request-form">
        <div class="composer-grid">
          <div class="group">
            <div class="field">
              <label for="title">Title</label>
              <span>Short task name used for the request heading and task folder slug.</span>
              <input id="title" name="title" maxlength="80" required>
              <div class="error-text" data-error-for="title"></div>
            </div>
            <div class="field">
              <label for="goal">Goal</label>
              <span>The concrete outcome you want from the implementation.</span>
              <textarea id="goal" name="goal" required></textarea>
              <div class="error-text" data-error-for="goal"></div>
            </div>
            <div class="field">
              <label for="background">Background / Why</label>
              <span>Context that helps the planner understand motivation and history.</span>
              <textarea id="background" name="background"></textarea>
              <div class="error-text" data-error-for="background"></div>
            </div>
          </div>
          <div class="group">
          <div class="field compact">
            <label for="target_repo">Target repo</label>
            <span>Choose a nearby repo from the dropdown or type any path manually.</span>
            <input id="target_repo" name="target_repo" list="target-repo-options" required>
            <datalist id="target-repo-options"></datalist>
            <div class="error-text" data-error-for="target_repo"></div>
            </div>
            <div class="field">
            <label for="base_branch">Base branch</label>
            <span>Branch used when preparing the isolated workspace.</span>
            <input id="base_branch" name="base_branch" required>
            <div class="error-text" data-error-for="base_branch"></div>
          </div>
          <div class="field compact">
            <label for="scope">Scope</label>
            <span>Starts with repo-based defaults; you can edit each line.</span>
            <textarea id="scope" name="scope"></textarea>
            <div class="error-text" data-error-for="scope"></div>
          </div>
          <div class="field compact">
            <label for="out_of_scope">Out of scope</label>
            <span>Pre-filled to keep changes inside the target repo; you can edit it.</span>
            <textarea id="out_of_scope" name="out_of_scope"></textarea>
            <div class="error-text" data-error-for="out_of_scope"></div>
          </div>
          <div class="field compact">
            <label for="constraints">Constraints / Notes</label>
            <span>Technical limits, deadlines, or environment rules.</span>
            <textarea id="constraints" name="constraints"></textarea>
            <div class="error-text" data-error-for="constraints"></div>
          </div>
          <div class="field compact">
            <label for="references">Reference files / paths</label>
            <span>One path per line.</span>
            <textarea id="references" name="references"></textarea>
            <div class="error-text" data-error-for="references"></div>
          </div>
          <div class="field compact">
            <label for="acceptance_criteria">Acceptance criteria</label>
            <span>One expected outcome per line.</span>
            <textarea id="acceptance_criteria" name="acceptance_criteria"></textarea>
            <div class="error-text" data-error-for="acceptance_criteria"></div>
          </div>
        </div>
        </div>
        <div class="form-actions">
          <button type="button" id="cancel-composer">Cancel</button>
          <button type="submit" id="submit-request" class="primary">Create request</button>
        </div>
      </form>
    </div>
  </section>
  <section id="settings-modal" class="modal" hidden aria-hidden="true">
    <div class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="settings-modal-title">
      <div class="modal-head">
        <div class="modal-copy">
          <h2 id="settings-modal-title">Model settings</h2>
          <p>Adjust runtime overrides for the planner, implementer, reviewer, and commit worker without leaving the board.</p>
        </div>
        <button type="button" id="close-settings" aria-label="Close model settings">Close</button>
      </div>
      <form id="settings-form" class="settings-shell">
        <div class="settings-copy">
          <strong>Runtime overrides</strong>
          <p>Leave a field blank to fall back to the configured agent default. Saving updates the in-memory runtime immediately and writes a local config file for future runs.</p>
        </div>
        <div class="settings-toolbar">
          <p id="settings-discovery-summary">Open the panel to load current model options.</p>
          <button type="button" id="refresh-model-options" class="ghost-button">Refresh discovered models</button>
        </div>
        <div class="settings-grid">
          <label class="settings-card" for="planner_model">
            <strong>Planner model</strong>
            <span>Used for plan generation and plan revisions.</span>
            <input id="planner_model" name="planner_model" list="opencode-model-options" placeholder="inherit default model">
            <small>Pick from discovered options or type any custom model value.</small>
          </label>
          <label class="settings-card" for="implementer_model">
            <strong>Implementer model</strong>
            <span>Used when coding inside the isolated workspace.</span>
            <input id="implementer_model" name="implementer_model" list="opencode-model-options" placeholder="inherit default model">
            <small>Pick from discovered options or type any custom model value.</small>
          </label>
          <label class="settings-card" for="reviewer_model">
            <strong>Reviewer model</strong>
            <span>Used for review verdicts before human verification.</span>
            <input id="reviewer_model" name="reviewer_model" list="opencode-model-options" placeholder="inherit default model">
            <small>Pick from discovered options or type any custom model value.</small>
          </label>
          <label class="settings-card" for="commit_model">
            <strong>Commit model</strong>
            <span>Used when generating the final commit message.</span>
            <input id="commit_model" name="commit_model" list="opencode-model-options" placeholder="inherit default model">
            <small>Pick from discovered options or type any custom model value.</small>
          </label>
        </div>
        <datalist id="opencode-model-options"></datalist>
        <div id="settings-config-path" class="settings-path">Config path: loading...</div>
        <div id="settings-status" class="settings-status">Current values load when you open this panel.</div>
        <div class="form-actions">
          <button type="button" id="cancel-settings">Cancel</button>
          <button type="submit" id="save-settings" class="primary">Save model settings</button>
        </div>
      </form>
    </div>
  </section>
  <section id="task-modal" class="modal" hidden aria-hidden="true">
    <div class="modal-panel" role="dialog" aria-modal="true" aria-labelledby="task-modal-title">
      <div class="modal-head">
        <div class="modal-copy">
          <h2 id="task-modal-title">Task details</h2>
          <p id="task-modal-subtitle">Inspect task metadata, markdown artifacts, and live agent logs.</p>
        </div>
        <button type="button" id="close-task-modal" aria-label="Close task details">Close</button>
      </div>
      <div id="task-modal-error" class="form-error" hidden></div>
      <div class="task-tabs">
        <button type="button" id="task-tab-overview" class="active">Overview</button>
        <button type="button" id="task-tab-editor" hidden>Viewer</button>
        <button type="button" id="task-tab-logs">Logs</button>
      </div>
      <section id="task-panel-overview" class="task-panel">
        <section id="task-verification-actions" class="verification-actions" hidden>
          <div class="editor-toolbar">
            <div>
              <strong id="task-verification-title">Human verification</strong>
              <div id="task-verification-status" class="muted">Manual verification actions appear here.</div>
            </div>
            <div>
              <button type="button" id="start-verification" hidden>Start verification</button>
              <button type="button" id="reject-verification" hidden>Reject to TODO</button>
              <button type="button" id="approve-verification" class="primary" hidden>Approve &amp; commit</button>
            </div>
          </div>
          <div id="task-verification-note-wrap" hidden>
            <label for="task-verification-note"><strong>Follow-up requirements</strong></label>
            <textarea id="task-verification-note" placeholder="Explain what must change before the task returns to TODO."></textarea>
          </div>
        </section>
        <div id="task-overview" class="muted">Select a task to inspect.</div>
      </section>
      <section id="task-panel-logs" class="task-panel" hidden>
        <div class="log-layout">
          <div id="task-log-files" class="log-file-list"></div>
          <pre id="task-log-viewer" class="log-viewer">Select a log file.</pre>
        </div>
      </section>
      <section id="task-panel-editor" class="task-panel" hidden>
        <div class="editor-toolbar">
          <div>
            <strong id="task-artifact-name">No document selected</strong>
            <div><span id="task-mode-badge" class="mode-pill">Viewer mode</span><span id="task-editor-status" class="muted">Select a markdown artifact to view.</span></div>
          </div>
          <div>
            <button type="button" id="toggle-plan-edit" hidden>Edit draft</button>
            <button type="button" id="save-plan" class="primary" hidden disabled>Save draft</button>
            <button type="button" id="approve-plan" hidden disabled>Approve plan</button>
          </div>
        </div>
        <div class="artifact-layout">
          <div id="task-markdown-files" class="log-file-list"></div>
          <div class="artifact-stage">
            <div id="task-viewer-host" class="viewer-host"></div>
            <div id="task-editor-host" class="editor-host" hidden></div>
            <textarea id="task-editor" class="editor-textarea" spellcheck="false" hidden disabled></textarea>
          </div>
        </div>
      </section>
    </div>
  </section>
  <main id="board"></main>
  <script src="https://uicdn.toast.com/editor/latest/toastui-editor-all.min.js"></script>
  <script>
    const board = document.getElementById('board');
    const body = document.body;
    const modal = document.getElementById('request-modal');
    const settingsModal = document.getElementById('settings-modal');
    const taskModal = document.getElementById('task-modal');
    const openComposerButton = document.getElementById('open-composer');
    const openSettingsButton = document.getElementById('open-settings');
    const closeComposerButton = document.getElementById('close-composer');
    const closeSettingsButton = document.getElementById('close-settings');
    const closeTaskModalButton = document.getElementById('close-task-modal');
    const cancelComposerButton = document.getElementById('cancel-composer');
    const cancelSettingsButton = document.getElementById('cancel-settings');
    const requestForm = document.getElementById('request-form');
    const settingsForm = document.getElementById('settings-form');
    const submitButton = document.getElementById('submit-request');
    const saveSettingsButton = document.getElementById('save-settings');
    const formError = document.getElementById('form-error');
    const formSuccess = document.getElementById('form-success');
    const targetRepoInput = document.getElementById('target_repo');
    const targetRepoOptions = document.getElementById('target-repo-options');
    const baseBranchInput = document.getElementById('base_branch');
    const plannerModelInput = document.getElementById('planner_model');
    const implementerModelInput = document.getElementById('implementer_model');
    const reviewerModelInput = document.getElementById('reviewer_model');
    const commitModelInput = document.getElementById('commit_model');
    const modelOptions = document.getElementById('opencode-model-options');
    const settingsConfigPath = document.getElementById('settings-config-path');
    const settingsDiscoverySummary = document.getElementById('settings-discovery-summary');
    const settingsStatus = document.getElementById('settings-status');
    const refreshModelOptionsButton = document.getElementById('refresh-model-options');
    const scopeField = document.getElementById('scope');
    const outOfScopeField = document.getElementById('out_of_scope');
    const defaultTargetRepo = {default_target_repo};
    const defaultBaseBranch = {default_base_branch};
    const taskModalError = document.getElementById('task-modal-error');
    const taskOverview = document.getElementById('task-overview');
    const taskTabOverview = document.getElementById('task-tab-overview');
    const taskTabLogs = document.getElementById('task-tab-logs');
    const taskTabEditor = document.getElementById('task-tab-editor');
    const taskPanelOverview = document.getElementById('task-panel-overview');
    const taskPanelLogs = document.getElementById('task-panel-logs');
    const taskPanelEditor = document.getElementById('task-panel-editor');
    const taskVerificationActions = document.getElementById('task-verification-actions');
    const taskVerificationStatus = document.getElementById('task-verification-status');
    const taskVerificationNoteWrap = document.getElementById('task-verification-note-wrap');
    const taskVerificationNote = document.getElementById('task-verification-note');
    const startVerificationButton = document.getElementById('start-verification');
    const rejectVerificationButton = document.getElementById('reject-verification');
    const approveVerificationButton = document.getElementById('approve-verification');
    const taskLogFiles = document.getElementById('task-log-files');
    const taskLogViewer = document.getElementById('task-log-viewer');
    const taskMarkdownFiles = document.getElementById('task-markdown-files');
    const taskViewerHost = document.getElementById('task-viewer-host');
    const taskEditorHost = document.getElementById('task-editor-host');
    const taskEditor = document.getElementById('task-editor');
    const taskArtifactName = document.getElementById('task-artifact-name');
    const taskModeBadge = document.getElementById('task-mode-badge');
    const taskEditorStatus = document.getElementById('task-editor-status');
    const togglePlanEditButton = document.getElementById('toggle-plan-edit');
    const savePlanButton = document.getElementById('save-plan');
    const approvePlanButton = document.getElementById('approve-plan');
    let lastAutoScope = '';
    let lastAutoOutOfScope = '';
    let activeTaskId = null;
    let activeTaskTab = 'overview';
    let activeTaskLogs = [];
    let activeTaskDetail = null;
    let activeArtifactName = null;
    let activeLogName = null;
    let logPollHandle = null;
    let runningTimerHandle = null;
    let planSourceMarkdown = '';
    let planEditMode = false;
    let planEditor = null;
    let markdownViewer = null;

    targetRepoInput.value = defaultTargetRepo;
    baseBranchInput.value = defaultBaseBranch;

    async function loadBoard() {{
      const res = await fetch('/api/board');
      const data = await res.json();
      board.innerHTML = data.columns.map((column) => `
        <section class="column">
          <h2>${{column.state}}</h2>
          ${{column.items.map((item) => `<article class="card"><button class="card-button" data-task-id="${{item.task_id}}"><strong>${{item.title}}</strong><div class="card-meta">${{item.task_id}}</div><div class="card-meta">iter ${{item.iteration}}</div>${{renderRunningMeta(item)}}${{renderCardModelMeta(item)}}</button></article>`).join('')}}
        </section>`).join('');
      refreshRunningClocks();
    }}

    function isActiveState(state) {{
      return ['planning', 'implementing', 'reviewing', 'human-verifying'].includes(state);
    }}

    function renderRunningMeta(item) {{
      if (!isActiveState(item.state) || !item.state_entered_at) return '';
      return `<div class="card-meta running" data-active-since="${{item.state_entered_at}}">running 00:00:00</div>`;
    }}

    function renderCardModelMeta(item) {{
      if (!item.active_model) return '';
      return `<div class="card-model"><strong>Current stage model used</strong>${{escapeHtml(item.active_model)}}</div>`;
    }}

    function formatElapsed(milliseconds) {{
      const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
      const hours = String(Math.floor(totalSeconds / 3600)).padStart(2, '0');
      const minutes = String(Math.floor((totalSeconds % 3600) / 60)).padStart(2, '0');
      const seconds = String(totalSeconds % 60).padStart(2, '0');
      return `${{hours}}:${{minutes}}:${{seconds}}`;
    }}

    function updateRunningClocks() {{
      const now = Date.now();
      board.querySelectorAll('[data-active-since]').forEach((node) => {{
        const since = Date.parse(node.dataset.activeSince || '');
        if (Number.isNaN(since)) return;
        node.textContent = `running ${{formatElapsed(now - since)}}`;
      }});
    }}

    function refreshRunningClocks() {{
      if (runningTimerHandle) clearInterval(runningTimerHandle);
      updateRunningClocks();
      runningTimerHandle = window.setInterval(updateRunningClocks, 1000);
    }}

    async function loadTargetRepoOptions() {{
      const response = await fetch('/api/target-repos');
      if (!response.ok) return;
      const data = await response.json();
      targetRepoOptions.innerHTML = data.items.map((item) => `<option value="${{item}}"></option>`).join('');
    }}

    function setSettingsModalOpen(isOpen) {{
      settingsModal.hidden = !isOpen;
      settingsModal.setAttribute('aria-hidden', String(!isOpen));
      syncBodyModalState();
      if (isOpen) plannerModelInput.focus();
    }}

    function setSettingsStatus(message, tone = 'neutral') {{
      settingsStatus.textContent = message;
      settingsStatus.dataset.tone = tone;
    }}

    function renderModelOptions(items) {{
      modelOptions.innerHTML = items.map((item) => `<option value="${{escapeHtml(item)}}"></option>`).join('');
    }}

    function updateModelDiscoverySummary(data) {{
      const count = data.available_models.length;
      if (count) {{
        const refreshedAt = data.discovered_at ? new Date(data.discovered_at).toLocaleString() : 'just now';
        settingsDiscoverySummary.textContent = `${{count}} discovered model${{count === 1 ? '' : 's'}} available. Suggestions stay editable, so you can still type any custom value. Last update: ${{refreshedAt}}.`;
        if (data.discovery_status === 'fallback' && data.discovery_error) {{
          setSettingsStatus(`Using cached model suggestions. Refresh failed: ${{data.discovery_error}}`, 'error');
        }} else {{
          setSettingsStatus('Current runtime overrides and discovered model suggestions are loaded.', 'success');
        }}
        return;
      }}
      settingsDiscoverySummary.textContent = 'No models are cached yet. You can still leave a field blank or type any custom model value manually.';
      if (data.discovery_status === 'error' && data.discovery_error) {{
        setSettingsStatus(`Model discovery failed: ${{data.discovery_error}}. Manual entry still works.`, 'error');
        return;
      }}
      if (data.discovery_status === 'empty') {{
        setSettingsStatus('OpenCode responded, but no model options were returned. Manual entry still works.');
        return;
      }}
      setSettingsStatus('Current runtime overrides are loaded. Refresh discovery when you want fresh model suggestions.');
    }}

    async function loadModelSettings(refresh = false) {{
      setSettingsStatus(refresh ? 'Refreshing discovered model options...' : 'Loading current model overrides...');
      refreshModelOptionsButton.disabled = true;
      try {{
        const response = await fetch(`/api/settings/models${{refresh ? '?refresh=true' : ''}}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Failed to load model settings.');
        plannerModelInput.value = data.planner_model || '';
        implementerModelInput.value = data.implementer_model || '';
        reviewerModelInput.value = data.reviewer_model || '';
        commitModelInput.value = data.commit_model || '';
        renderModelOptions(data.available_models || []);
        settingsConfigPath.textContent = `Config path: ${{data.config_path}}`;
        updateModelDiscoverySummary(data);
      }} finally {{
        refreshModelOptionsButton.disabled = false;
      }}
    }}

    async function openSettingsModal() {{
      setSettingsModalOpen(true);
      try {{
        await loadModelSettings();
      }} catch (error) {{
        setSettingsStatus(error.message, 'error');
      }}
    }}

    async function saveModelSettings(event) {{
      event.preventDefault();
      saveSettingsButton.disabled = true;
      setSettingsStatus('Saving model overrides...');
      try {{
        const response = await fetch('/api/settings/models', {{
          method: 'PUT',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{
            planner_model: plannerModelInput.value,
            implementer_model: implementerModelInput.value,
            reviewer_model: reviewerModelInput.value,
            commit_model: commitModelInput.value,
          }}),
        }});
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Failed to save model settings.');
        plannerModelInput.value = data.planner_model || '';
        implementerModelInput.value = data.implementer_model || '';
        reviewerModelInput.value = data.reviewer_model || '';
        commitModelInput.value = data.commit_model || '';
        settingsConfigPath.textContent = `Config path: ${{data.config_path}}`;
        setSettingsStatus(`Saved runtime config to ${{data.config_path}}.`, 'success');
      }} catch (error) {{
        setSettingsStatus(error.message, 'error');
      }} finally {{
        saveSettingsButton.disabled = false;
      }}
    }}

    function setModalOpen(isOpen) {{
      modal.hidden = !isOpen;
      modal.setAttribute('aria-hidden', String(!isOpen));
      syncBodyModalState();
      if (isOpen) document.getElementById('title').focus();
    }}

    function setTaskModalOpen(isOpen) {{
      taskModal.hidden = !isOpen;
      taskModal.setAttribute('aria-hidden', String(!isOpen));
      syncBodyModalState();
      if (isOpen) maybeStartLogPolling();
      else stopLogPolling();
    }}

    function syncBodyModalState() {{
      body.classList.toggle('modal-open', !modal.hidden || !settingsModal.hidden || !taskModal.hidden);
    }}

    function clearMessages() {{
      formError.hidden = true;
      formError.textContent = '';
      formSuccess.hidden = true;
      formSuccess.textContent = '';
      document.querySelectorAll('[data-error-for]').forEach((node) => {{ node.textContent = ''; }});
    }}

    function normalizeRepoPath(value) {{
      return (value || '').toString().trim().replace(/\\/+$/, '');
    }}

    function deriveRepoContext(path) {{
      const normalized = normalizeRepoPath(path);
      const segments = normalized.split('/').filter(Boolean);
      const repoName = segments.length ? segments[segments.length - 1] : 'target repo';
      const parentName = segments.length > 1 ? segments[segments.length - 2] : null;
      return {{ normalized, repoName, parentName }};
    }}

    function buildScopeDefaults(path) {{
      const context = deriveRepoContext(path);
      const lines = [
        `Limit code changes to \\`${{context.normalized}}\\`.`,
        `Modify only the files under \\`${{context.normalized}}\\` that are needed for this request.`,
        `Keep tests and local configuration changes scoped to \\`${{context.normalized}}\\`.`,
      ];
      if (context.repoName && context.repoName !== 'target repo') lines.push(`Focus on the \\`${{context.repoName}}\\` project or app.`);
      return lines.join('\\n');
    }}

    function buildOutOfScopeDefaults(path) {{
      const context = deriveRepoContext(path);
      const lines = [
        `Do not modify files outside \\`${{context.normalized}}\\`.`,
        'Do not change unrelated apps, packages, or workspace-wide configuration.',
        'Do not add deployment or infrastructure changes unless the request explicitly asks for them.',
      ];
      if (context.parentName) lines.push(`Do not modify sibling projects under \\`${{context.parentName}}/\\` unless the request explicitly requires it.`);
      return lines.join('\\n');
    }}

    function canReplaceAutofill(field, nextValue, lastValue) {{
      return !field.value.trim() || field.dataset.autofilled === 'true' || field.value === lastValue || field.value === nextValue;
    }}

    function applyRepoDefaults() {{
      const repoPath = normalizeRepoPath(targetRepoInput.value);
      if (!repoPath) return;
      const nextScope = buildScopeDefaults(repoPath);
      const nextOutOfScope = buildOutOfScopeDefaults(repoPath);
      if (canReplaceAutofill(scopeField, nextScope, lastAutoScope)) {{
        scopeField.value = nextScope;
        scopeField.dataset.autofilled = 'true';
      }}
      if (canReplaceAutofill(outOfScopeField, nextOutOfScope, lastAutoOutOfScope)) {{
        outOfScopeField.value = nextOutOfScope;
        outOfScopeField.dataset.autofilled = 'true';
      }}
      lastAutoScope = nextScope;
      lastAutoOutOfScope = nextOutOfScope;
    }}

    function resetFormState() {{
      requestForm.reset();
      targetRepoInput.value = defaultTargetRepo;
      baseBranchInput.value = defaultBaseBranch;
      scopeField.dataset.autofilled = 'true';
      outOfScopeField.dataset.autofilled = 'true';
      applyRepoDefaults();
    }}

    function escapeHtml(value) {{
      return (value || '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
    }}

    function setTaskTab(tab) {{
      activeTaskTab = tab;
      taskTabOverview.classList.toggle('active', tab === 'overview');
      taskTabEditor.classList.toggle('active', tab === 'editor');
      taskTabLogs.classList.toggle('active', tab === 'logs');
      taskPanelOverview.hidden = tab !== 'overview';
      taskPanelEditor.hidden = tab !== 'editor';
      taskPanelLogs.hidden = tab !== 'logs';
      if (tab === 'logs' && activeTaskId) loadTaskLogs(activeTaskId);
      if (tab === 'editor' && activeTaskId) loadMarkdownArtifact(activeTaskId, activeArtifactName);
      maybeStartLogPolling();
    }}

    function ensureMarkdownViewer(value) {{
      if (window.toastui && window.toastui.Editor && window.toastui.Editor.factory) {{
        taskViewerHost.innerHTML = '';
        markdownViewer = window.toastui.Editor.factory({{
          el: taskViewerHost,
          viewer: true,
          initialValue: value || '',
        }});
        return markdownViewer;
      }}
      taskViewerHost.innerHTML = `<pre class="log-viewer">${{escapeHtml(value || '')}}</pre>`;
      return null;
    }}

    function ensurePlanEditor() {{
      if (planEditor || !window.toastui || !window.toastui.Editor) return planEditor;
      planEditor = new window.toastui.Editor({{
        el: taskEditorHost,
        height: '420px',
        initialEditType: 'markdown',
        previewStyle: 'tab',
        hideModeSwitch: true,
        toolbarItems: [
          ['heading', 'bold', 'italic'],
          ['ul', 'ol', 'task'],
          ['link', 'quote', 'code'],
        ],
        usageStatistics: false,
      }});
      return planEditor;
    }}

    function setPlanEditorContent(value) {{
      ensureMarkdownViewer(value || '');
      if (planEditor) planEditor.setMarkdown(value || '');
      taskEditor.value = value || '';
    }}

    function getPlanEditorContent() {{
      const editor = ensurePlanEditor();
      if (editor) return editor.getMarkdown();
      return taskEditor.value;
    }}

    function setArtifactMode(editing) {{
      taskModeBadge.textContent = editing ? 'Edit mode' : 'Viewer mode';
      taskViewerHost.hidden = editing;
      taskEditorHost.hidden = !editing;
      if (editing) {{
        const editor = ensurePlanEditor();
        if (editor) {{
          editor.setMarkdown(taskEditor.value || planSourceMarkdown || '');
          taskEditor.hidden = true;
          taskEditorHost.hidden = false;
        }} else {{
          taskEditor.hidden = false;
          taskEditor.disabled = false;
          taskEditor.readOnly = false;
        }}
      }} else {{
        taskEditorHost.hidden = true;
        taskEditor.hidden = true;
        taskEditor.disabled = true;
        taskEditor.readOnly = true;
      }}
    }}

    function isPlanDirty() {{
      return getPlanEditorContent().replace(/\\s+$/, '') !== planSourceMarkdown.replace(/\\s+$/, '');
    }}

    function updatePlanActionState() {{
      const editableArtifact = Boolean(activeTaskDetail && activeTaskDetail.metadata.state === 'waiting-check-plans' && activeArtifactName === 'PLAN.md');
      togglePlanEditButton.hidden = !editableArtifact;
      savePlanButton.hidden = !editableArtifact || !planEditMode;
      approvePlanButton.hidden = !editableArtifact;
      togglePlanEditButton.textContent = planEditMode ? 'Back to viewer' : 'Edit PLAN.md';
      savePlanButton.disabled = !editableArtifact || !planEditMode;
      approvePlanButton.disabled = !editableArtifact;
    }}

    function updateHumanVerificationState() {{
      const state = activeTaskDetail?.metadata?.state;
      const canStart = state === 'completed-reviews';
      const canVerify = state === 'human-verifying';
      taskVerificationActions.hidden = !(canStart || canVerify);
      startVerificationButton.hidden = !canStart;
      rejectVerificationButton.hidden = !canVerify;
      approveVerificationButton.hidden = !canVerify;
      taskVerificationNoteWrap.hidden = !canVerify;
      rejectVerificationButton.disabled = !canVerify || !taskVerificationNote.value.trim();
      approveVerificationButton.disabled = !canVerify;
      if (canStart) {{
        taskVerificationStatus.textContent = 'AI review passed. Start verification to apply the workspace patch to the target repo for manual checking.';
      }} else if (canVerify) {{
        taskVerificationStatus.textContent = 'Patch is applied in the target repo. Reject rolls it back and sends the task back to TODO; approve commits and completes the task.';
      }} else {{
        taskVerificationStatus.textContent = 'Manual verification actions appear here.';
      }}
    }}

    function stopLogPolling() {{
      if (logPollHandle) {{
        clearInterval(logPollHandle);
        logPollHandle = null;
      }}
    }}

    function maybeStartLogPolling() {{
      stopLogPolling();
      if (taskModal.hidden || taskPanelLogs.hidden || !activeTaskId) return;
      logPollHandle = window.setInterval(() => {{
        loadTaskLogs(activeTaskId, true);
      }}, 500);
    }}

    function renderTaskOverview(detail) {{
      const metadata = detail.metadata;
      activeTaskDetail = detail;
      const latestError = metadata.errors.length ? metadata.errors[metadata.errors.length - 1] : null;
      const viewerVisible = detail.markdown_files.length > 0;
      const planEditable = metadata.state === 'waiting-check-plans' && detail.markdown_files.includes('PLAN.md');
      const stageModels = [
        {{ label: 'Planner model used', value: metadata.plan.resolved_model, note: 'Captured from the plan run output.' }},
        {{ label: 'Implementer model used', value: metadata.implementation.resolved_model, note: 'Captured from the workspace implementation run.' }},
        {{ label: 'Reviewer model used', value: metadata.review.resolved_model, note: 'Captured from the AI review run.' }},
      ];
      taskTabEditor.hidden = !viewerVisible;
      if (!viewerVisible && taskTabEditor.classList.contains('active')) setTaskTab('overview');
      if (!activeArtifactName || !detail.markdown_files.includes(activeArtifactName)) activeArtifactName = preferredArtifact(detail.markdown_files);
      planEditMode = false;
      taskModeBadge.textContent = 'Viewer mode';
      taskEditorStatus.textContent = planEditable ? 'Rendered markdown preview. Use Edit PLAN.md only when you want to change the document.' : 'Rendered markdown preview only for this task state.';
      updatePlanActionState();
      updateHumanVerificationState();
      renderArtifactButtons(detail.markdown_files);
      taskOverview.innerHTML = `
        <div class="task-meta-grid">
          <div class="meta-item"><span>Title</span><strong>${{escapeHtml(metadata.title)}}</strong></div>
          <div class="meta-item"><span>Task ID</span><strong>${{escapeHtml(metadata.task_id)}}</strong></div>
          <div class="meta-item"><span>State</span><strong>${{escapeHtml(metadata.state)}}</strong></div>
          <div class="meta-item"><span>Request language</span><strong>${{escapeHtml(metadata.request.language || 'en')}}</strong></div>
          <div class="meta-item"><span>Updated</span><strong>${{escapeHtml(metadata.updated_at)}}</strong></div>
          <div class="meta-item"><span>Target repo</span><strong>${{escapeHtml(metadata.target.repo_root)}}</strong></div>
          <div class="meta-item"><span>Base branch</span><strong>${{escapeHtml(metadata.target.base_branch)}}</strong></div>
        </div>
        <div class="task-section">
          <h3>Captured stage models</h3>
          <div class="task-model-grid">${{stageModels.map((item) => `<div class="task-model-row"><span>${{escapeHtml(item.label)}}</span><div><strong>${{escapeHtml(item.value || 'Not captured yet')}}</strong><small>${{escapeHtml(item.note)}} This is the actual model used, separate from runtime override settings.</small></div></div>`).join('')}}</div>
        </div>
        <div class="task-section">
          <h3>Markdown files</h3>
          <ul class="task-list">${{detail.markdown_files.length ? detail.markdown_files.map((file) => `<li>${{escapeHtml(file)}}</li>`).join('') : '<li>No markdown files</li>'}}</ul>
        </div>
        <div class="task-section">
          <h3>JSON files</h3>
          <ul class="task-list">${{detail.json_files.length ? detail.json_files.map((file) => `<li>${{escapeHtml(file)}}</li>`).join('') : '<li>No JSON files</li>'}}</ul>
        </div>
        <div class="task-section">
          <h3>Log files</h3>
          <ul class="task-list">${{detail.log_files.length ? detail.log_files.map((file) => `<li>${{escapeHtml(file)}}</li>`).join('') : '<li>No logs yet</li>'}}</ul>
        </div>
        <div class="task-section">
          <h3>Latest error</h3>
          <div class="muted">${{latestError ? escapeHtml(latestError.message) : 'No recorded errors.'}}</div>
        </div>
      `;
      document.getElementById('task-modal-title').textContent = metadata.title;
      document.getElementById('task-modal-subtitle').textContent = `${{metadata.task_id}} in ${{metadata.state}}`;
    }}

    function preferredArtifact(files) {{
      if (files.includes('PLAN.md')) return 'PLAN.md';
      return files[files.length - 1] || null;
    }}

    function renderArtifactButtons(files) {{
      if (!files.length) {{
        taskMarkdownFiles.innerHTML = '<div class="muted">No markdown artifacts yet.</div>';
        taskArtifactName.textContent = 'No document selected';
        return;
      }}
      taskMarkdownFiles.innerHTML = files.map((file, index) => `<button type="button" class="${{file === activeArtifactName ? 'active' : ''}}" data-artifact-index="${{index}}">${{escapeHtml(file)}}</button>`).join('');
      taskArtifactName.textContent = activeArtifactName || 'No document selected';
    }}

    function renderTaskLogEntries(entries) {{
      activeTaskLogs = entries;
      if (!entries.length) {{
        activeLogName = null;
        taskLogFiles.innerHTML = '<div class="muted">No logs yet.</div>';
        taskLogViewer.textContent = 'No logs yet.';
        return;
      }}
      if (!activeLogName || !entries.some((entry) => entry.name === activeLogName)) activeLogName = entries[entries.length - 1].name;
      taskLogFiles.innerHTML = entries.map((entry, index) => `<button type="button" class="${{entry.name === activeLogName ? 'active' : ''}}" data-log-index="${{index}}">${{escapeHtml(entry.name)}}</button>`).join('');
      showLogEntry(entries.findIndex((entry) => entry.name === activeLogName), true);
    }}

    function showLogEntry(index, scrollToBottom = false) {{
      const entry = activeTaskLogs[index];
      if (!entry) return;
      activeLogName = entry.name;
      taskLogViewer.textContent = entry.rendered_content || entry.content || '(empty log file)';
      taskLogFiles.querySelectorAll('button').forEach((button, buttonIndex) => {{
        button.classList.toggle('active', buttonIndex === index);
      }});
      if (scrollToBottom) taskLogViewer.scrollTop = taskLogViewer.scrollHeight;
    }}

    function appendRealtimeLog(eventPayload) {{
      const payload = eventPayload.payload || eventPayload;
      if (!payload || !payload.log_name || typeof payload.raw_line !== 'string') return;
      let entry = activeTaskLogs.find((item) => item.name === payload.log_name);
      if (!entry) {{
        entry = {{ name: payload.log_name, path: payload.log_name, content: '', rendered_content: '', updated_at: new Date().toISOString() }};
        activeTaskLogs = [...activeTaskLogs, entry];
      }}
      entry.content = `${{entry.content || ''}}${{payload.raw_line}}\n`;
      if (typeof payload.rendered_line === 'string' && payload.rendered_line.trim()) {{
        entry.rendered_content = `${{entry.rendered_content || ''}}${{payload.rendered_line}}\n\n`;
      }}
      entry.updated_at = new Date().toISOString();
      renderTaskLogEntries(activeTaskLogs);
    }}

    async function loadTaskDetail(taskId, preserveTab = false) {{
      const nextTab = preserveTab ? activeTaskTab : 'editor';
      activeTaskId = taskId;
      activeTaskLogs = [];
      activeLogName = null;
      activeTaskDetail = null;
      taskModalError.hidden = true;
      taskOverview.innerHTML = '<div class="muted">Loading task details...</div>';
      taskLogFiles.innerHTML = '';
      taskLogViewer.textContent = 'Select the Logs tab to load OpenCode output.';
      taskMarkdownFiles.innerHTML = '';
      taskArtifactName.textContent = 'No document selected';
      setPlanEditorContent('');
      setArtifactMode(false);
      activeArtifactName = null;
      planEditMode = false;
      savePlanButton.disabled = true;
      taskModeBadge.textContent = 'Viewer mode';
      taskEditorStatus.textContent = 'Select a markdown artifact to view.';
      taskVerificationNote.value = '';
      updateHumanVerificationState();
      taskTabEditor.hidden = true;
      setTaskTab(nextTab);
      setTaskModalOpen(true);
      try {{
        const response = await fetch(`/api/tasks/${{taskId}}`);
        const detail = await response.json();
        if (!response.ok) throw new Error(detail.detail || 'Failed to load task details.');
        renderTaskOverview(detail);
        if (nextTab === 'editor' && detail.markdown_files.length) loadMarkdownArtifact(taskId, activeArtifactName);
        if (nextTab === 'logs') loadTaskLogs(taskId, true);
      }} catch (error) {{
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        taskOverview.innerHTML = '<div class="muted">Unable to load task details.</div>';
      }}
    }}

    async function loadTaskLogs(taskId, silent = false) {{
      if (!silent) {{
        taskLogFiles.innerHTML = '<div class="muted">Loading logs...</div>';
        taskLogViewer.textContent = 'Loading logs...';
      }}
      try {{
        const response = await fetch(`/api/tasks/${{taskId}}/logs`);
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Failed to load task logs.');
        renderTaskLogEntries(payload.entries);
      }} catch (error) {{
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        taskLogFiles.innerHTML = '<div class="muted">Failed to load logs.</div>';
        taskLogViewer.textContent = 'Failed to load logs.';
      }}
    }}

    async function loadMarkdownArtifact(taskId, filename = null) {{
      if (!activeTaskDetail || !activeTaskDetail.markdown_files.length) return;
      activeArtifactName = filename && activeTaskDetail.markdown_files.includes(filename) ? filename : preferredArtifact(activeTaskDetail.markdown_files);
      renderArtifactButtons(activeTaskDetail.markdown_files);
      taskArtifactName.textContent = activeArtifactName || 'No document selected';
      taskEditorStatus.textContent = activeArtifactName ? `Loading ${{activeArtifactName}}...` : 'No markdown artifact selected.';
      setArtifactMode(false);
      updatePlanActionState();
      try {{
        const response = await fetch(`/api/tasks/${{taskId}}/artifacts/${{activeArtifactName}}`);
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || `Failed to load ${{activeArtifactName}}.`);
        planSourceMarkdown = payload.content;
        setPlanEditorContent(payload.content);
        const editable = activeTaskDetail.metadata.state === 'waiting-check-plans' && activeArtifactName === 'PLAN.md' && planEditMode;
        setArtifactMode(editable);
        updatePlanActionState();
        if (activeArtifactName === 'PLAN.md' && activeTaskDetail.metadata.state === 'waiting-check-plans') {{
          taskEditorStatus.textContent = planEditMode ? 'Editing PLAN.md markdown. Save your draft before approval.' : 'Viewing rendered PLAN.md. Use Edit PLAN.md to switch into editing.';
        }} else {{
          taskEditorStatus.textContent = `${{activeArtifactName}} is shown as a rendered markdown document.`;
        }}
      }} catch (error) {{
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        taskEditorStatus.textContent = `Unable to load ${{activeArtifactName || 'artifact'}}.`;
      }}
    }}

    async function togglePlanEditMode() {{
      if (!activeTaskDetail || activeTaskDetail.metadata.state !== 'waiting-check-plans' || activeArtifactName !== 'PLAN.md') return;
      planEditMode = !planEditMode;
      setArtifactMode(planEditMode);
      updatePlanActionState();
      await loadMarkdownArtifact(activeTaskId, activeArtifactName);
    }}

    async function savePlanArtifact() {{
      if (!activeTaskId) return;
      savePlanButton.disabled = true;
      taskEditorStatus.textContent = 'Saving PLAN.md...';
      try {{
        const response = await fetch(`/api/tasks/${{activeTaskId}}/artifacts/PLAN.md`, {{
          method: 'PUT',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ content: getPlanEditorContent() }}),
        }});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Failed to save PLAN.md.');
        planSourceMarkdown = getPlanEditorContent();
        taskEditorStatus.textContent = 'Saved PLAN.md.';
      }} catch (error) {{
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        taskEditorStatus.textContent = 'Save failed.';
      }} finally {{
        updatePlanActionState();
      }}
    }}

    async function approvePlan() {{
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'waiting-check-plans') return;
      savePlanButton.disabled = true;
      approvePlanButton.disabled = true;
      taskEditorStatus.textContent = isPlanDirty() ? 'Saving PLAN.md before approval...' : 'Approving plan...';
      try {{
        if (isPlanDirty()) await savePlanArtifact();
        const response = await fetch(`/api/tasks/${{activeTaskId}}/approve-plan`, {{ method: 'POST' }});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Failed to approve plan.');
        taskEditorStatus.textContent = 'Plan approved.';
        await loadBoard();
        setTaskModalOpen(false);
      }} catch (error) {{
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        taskEditorStatus.textContent = 'Approval failed.';
      }} finally {{
        updatePlanActionState();
      }}
    }}

    async function startVerification() {{
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'completed-reviews') return;
      startVerificationButton.disabled = true;
      taskVerificationStatus.textContent = 'Applying patch to target repo and starting human verification...';
      try {{
        const response = await fetch(`/api/tasks/${{activeTaskId}}/start-verification`, {{ method: 'POST' }});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Failed to start verification.');
        await loadBoard();
        await loadTaskDetail(activeTaskId, true);
      }} catch (error) {{
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        taskVerificationStatus.textContent = 'Failed to start verification.';
      }} finally {{
        updateHumanVerificationState();
      }}
    }}

    async function rejectVerification() {{
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'human-verifying') return;
      const note = taskVerificationNote.value.trim();
      if (!note) return;
      rejectVerificationButton.disabled = true;
      approveVerificationButton.disabled = true;
      taskVerificationStatus.textContent = 'Rolling back patch and sending task back to TODO...';
      try {{
        const response = await fetch(`/api/tasks/${{activeTaskId}}/reject-verification`, {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ note }}),
        }});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Failed to reject verification.');
        taskVerificationNote.value = '';
        await loadBoard();
        setTaskModalOpen(false);
      }} catch (error) {{
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        taskVerificationStatus.textContent = 'Failed to reject verification.';
      }} finally {{
        updateHumanVerificationState();
      }}
    }}

    async function approveVerification() {{
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'human-verifying') return;
      rejectVerificationButton.disabled = true;
      approveVerificationButton.disabled = true;
      taskVerificationStatus.textContent = 'Creating commit in target repo...';
      try {{
        const response = await fetch(`/api/tasks/${{activeTaskId}}/approve-verification`, {{ method: 'POST' }});
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Failed to approve verification.');
        taskVerificationNote.value = '';
        await loadBoard();
        setTaskModalOpen(false);
      }} catch (error) {{
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        taskVerificationStatus.textContent = 'Failed to approve verification.';
      }} finally {{
        updateHumanVerificationState();
      }}
    }}

    function validateForm() {{
      const data = new FormData(requestForm);
      const errors = {{}};
      const title = (data.get('title') || '').toString().trim();
      const goal = (data.get('goal') || '').toString().trim();
      const targetRepo = normalizeRepoPath(data.get('target_repo'));
      const baseBranch = (data.get('base_branch') || '').toString().trim();
      if (title.length < 5) errors.title = 'Use at least 5 characters.';
      if (!goal) errors.goal = 'Goal is required.';
      if (!targetRepo) errors.target_repo = 'Target repo is required.';
      if (!baseBranch) errors.base_branch = 'Base branch is required.';
      document.querySelectorAll('[data-error-for]').forEach((node) => {{
        node.textContent = errors[node.dataset.errorFor] || '';
      }});
      return Object.keys(errors).length === 0;
    }}

    async function submitRequest(event) {{
      event.preventDefault();
      clearMessages();
      applyRepoDefaults();
      if (!validateForm()) return;
      const payload = Object.fromEntries(new FormData(requestForm).entries());
      submitButton.disabled = true;
      submitButton.textContent = 'Creating...';
      try {{
        const response = await fetch('/api/requests', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(payload),
        }});
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Request creation failed.');
        formSuccess.hidden = false;
        formSuccess.textContent = `Created request at ${{data.task_path}}`;
        resetFormState();
        setModalOpen(false);
        await loadBoard();
      }} catch (error) {{
        formError.hidden = false;
        formError.textContent = error.message;
      }} finally {{
        submitButton.disabled = false;
        submitButton.textContent = 'Create request';
      }}
    }}

    document.getElementById('refresh').addEventListener('click', loadBoard);
    openComposerButton.addEventListener('click', () => {{ clearMessages(); setModalOpen(true); }});
    openSettingsButton.addEventListener('click', openSettingsModal);
    closeComposerButton.addEventListener('click', () => {{ clearMessages(); setModalOpen(false); }});
    closeSettingsButton.addEventListener('click', () => setSettingsModalOpen(false));
    cancelComposerButton.addEventListener('click', () => {{ clearMessages(); resetFormState(); setModalOpen(false); }});
    cancelSettingsButton.addEventListener('click', () => setSettingsModalOpen(false));
    modal.addEventListener('click', (event) => {{ if (event.target === modal) setModalOpen(false); }});
    settingsModal.addEventListener('click', (event) => {{ if (event.target === settingsModal) setSettingsModalOpen(false); }});
    closeTaskModalButton.addEventListener('click', () => {{ stopLogPolling(); setTaskModalOpen(false); }});
    taskModal.addEventListener('click', (event) => {{ if (event.target === taskModal) {{ stopLogPolling(); setTaskModalOpen(false); }} }});
    document.addEventListener('keydown', (event) => {{ if (event.key === 'Escape' && !modal.hidden) setModalOpen(false); if (event.key === 'Escape' && !settingsModal.hidden) setSettingsModalOpen(false); if (event.key === 'Escape' && !taskModal.hidden) {{ stopLogPolling(); setTaskModalOpen(false); }} }});
    requestForm.addEventListener('submit', submitRequest);
    settingsForm.addEventListener('submit', saveModelSettings);
    refreshModelOptionsButton.addEventListener('click', () => loadModelSettings(true).catch((error) => setSettingsStatus(error.message, 'error')));
    board.addEventListener('click', (event) => {{ const button = event.target.closest('[data-task-id]'); if (!button) return; loadTaskDetail(button.dataset.taskId); }});
    taskTabOverview.addEventListener('click', () => setTaskTab('overview'));
    taskTabLogs.addEventListener('click', () => setTaskTab('logs'));
    taskTabEditor.addEventListener('click', () => setTaskTab('editor'));
    taskMarkdownFiles.addEventListener('click', (event) => {{ const button = event.target.closest('[data-artifact-index]'); if (!button || !activeTaskDetail) return; const file = activeTaskDetail.markdown_files[Number(button.dataset.artifactIndex)]; if (!file) return; planEditMode = false; loadMarkdownArtifact(activeTaskId, file); }});
    taskLogFiles.addEventListener('click', (event) => {{ const button = event.target.closest('[data-log-index]'); if (!button) return; showLogEntry(Number(button.dataset.logIndex)); }});
    togglePlanEditButton.addEventListener('click', togglePlanEditMode);
    savePlanButton.addEventListener('click', savePlanArtifact);
    approvePlanButton.addEventListener('click', approvePlan);
    taskVerificationNote.addEventListener('input', updateHumanVerificationState);
    startVerificationButton.addEventListener('click', startVerification);
    rejectVerificationButton.addEventListener('click', rejectVerification);
    approveVerificationButton.addEventListener('click', approveVerification);
    ['title', 'goal', 'target_repo', 'base_branch'].forEach((name) => {{ requestForm.elements[name].addEventListener('blur', validateForm); }});
    targetRepoInput.addEventListener('input', applyRepoDefaults);
    targetRepoInput.addEventListener('change', applyRepoDefaults);
    scopeField.addEventListener('input', () => {{ scopeField.dataset.autofilled = 'false'; }});
    outOfScopeField.addEventListener('input', () => {{ outOfScopeField.dataset.autofilled = 'false'; }});
    resetFormState();
    loadTargetRepoOptions();
    const source = new EventSource('/api/events');
    source.addEventListener('board_snapshot', loadBoard);
    source.addEventListener('task_moved', async (event) => {{
      await loadBoard();
      const payload = JSON.parse(event.data);
      if (taskModal.hidden || activeTaskId !== payload.task_id) return;
      await loadTaskDetail(activeTaskId, true);
    }});
    source.addEventListener('worker_log', (event) => {{
      if (taskModal.hidden) return;
      const payload = JSON.parse(event.data);
      if (activeTaskId !== payload.task_id) return;
      appendRealtimeLog(payload);
    }});
    loadBoard();
  </script>
</body>
</html>
"""

    return router
