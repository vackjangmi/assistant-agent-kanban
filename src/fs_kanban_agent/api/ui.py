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
    :root {{ --bg-top: #f7f2e8; --bg-bottom: #e8eef5; --panel: rgba(255,255,255,0.78); --border: rgba(24,32,38,0.15); --accent: #7c4f2c; --accent-strong: #5f3417; --danger: #a33a2a; --text: #182026; --muted: #53616c; --shadow: 0 18px 40px rgba(0,0,0,0.12); }}
    * {{ box-sizing: border-box; }}
    body {{ font-family: Georgia, serif; margin: 0; background: linear-gradient(180deg, var(--bg-top), var(--bg-bottom)); color: var(--text); }}
    body.modal-open {{ overflow: hidden; }}
    header {{ padding: 16px 20px; display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }}
    .header-actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}
    button {{ padding: 9px 14px; border: 1px solid var(--text); background: #fff9ef; cursor: pointer; font: inherit; }}
    button.primary {{ background: var(--accent); border-color: var(--accent-strong); color: #fff; }}
    button:disabled {{ opacity: 0.7; cursor: progress; }}
    #board {{ display: grid; grid-template-columns: repeat(5, minmax(220px, 1fr)); gap: 12px; padding: 0 20px 20px; }}
    .column {{ background: var(--panel); border: 1px solid var(--border); padding: 12px; min-height: 160px; }}
    .column h2 {{ margin-top: 0; }}
    .card {{ background: white; border-left: 4px solid var(--accent); padding: 10px; margin: 10px 0; box-shadow: 0 4px 10px rgba(0,0,0,0.05); }}
    .card-button {{ width: 100%; text-align: left; border: 0; background: transparent; padding: 0; cursor: pointer; color: inherit; }}
    .card:hover {{ transform: translateY(-1px); box-shadow: 0 8px 20px rgba(0,0,0,0.08); }}
    .card-meta {{ color: var(--muted); font-size: 0.95rem; }}
    .card-meta.running {{ color: var(--accent-strong); font-variant-numeric: tabular-nums; }}
    .modal {{ position: fixed; inset: 0; display: flex; align-items: center; justify-content: center; padding: 24px; background: rgba(24,32,38,0.36); backdrop-filter: blur(4px); }}
    .modal[hidden] {{ display: none; }}
    .modal-panel {{ width: min(1040px, 100%); max-height: calc(100vh - 48px); overflow: auto; background: rgba(255,255,255,0.95); border: 1px solid var(--border); box-shadow: var(--shadow); padding: 22px; }}
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
    .form-actions {{ display: flex; justify-content: flex-end; gap: 10px; margin-top: 16px; }}
    .task-meta-grid {{ display: grid; grid-template-columns: repeat(2, minmax(220px, 1fr)); gap: 10px 16px; margin-bottom: 18px; }}
    .meta-item span {{ display: block; color: var(--muted); font-size: 0.9rem; }}
    .meta-item strong {{ display: block; margin-top: 2px; }}
    .task-tabs {{ display: flex; gap: 8px; margin-bottom: 14px; }}
    .task-tabs button.active {{ background: var(--accent); color: #fff; border-color: var(--accent-strong); }}
    .task-panel[hidden] {{ display: none; }}
    .task-section {{ margin-bottom: 16px; }}
    .task-section h3 {{ margin-bottom: 8px; }}
    .task-list {{ margin: 0; padding-left: 18px; color: var(--muted); }}
    .log-layout {{ display: grid; grid-template-columns: minmax(220px, 280px) 1fr; gap: 14px; }}
    .log-file-list {{ display: grid; gap: 8px; align-content: start; }}
    .log-file-list button {{ text-align: left; }}
    .log-file-list button.active {{ background: var(--accent); color: #fff; border-color: var(--accent-strong); }}
    .log-viewer {{ min-height: 320px; max-height: 50vh; overflow: auto; border: 1px solid var(--border); background: rgba(248,246,240,0.95); padding: 12px; white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9rem; }}
    .muted {{ color: var(--muted); }}
    .editor-toolbar {{ display: flex; justify-content: space-between; gap: 10px; margin-bottom: 10px; align-items: center; flex-wrap: wrap; }}
    .artifact-layout {{ display: grid; grid-template-columns: minmax(220px, 280px) 1fr; gap: 14px; }}
    .editor-textarea {{ width: 100%; min-height: 360px; resize: vertical; border: 1px solid var(--border); background: rgba(255,255,255,0.98); padding: 12px; font: inherit; }}
    .editor-host {{ min-height: 420px; border: 1px solid var(--border); background: #fff; }}
    .editor-host[hidden] {{ display: none; }}
    @media (max-width: 900px) {{ #board, .composer-grid, .task-meta-grid, .log-layout, .artifact-layout {{ grid-template-columns: 1fr; }} .modal {{ padding: 12px; align-items: stretch; }} .modal-panel {{ max-height: none; }} .form-actions {{ flex-direction: column-reverse; }} .form-actions button {{ width: 100%; }} }}
  </style>
</head>
<body>
  <header>
    <h1>Filesystem Kanban Agent</h1>
    <div class="header-actions">
      <button id="open-composer" class="primary">New request</button>
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
            <div id="task-editor-status" class="muted">Select a markdown artifact to view.</div>
          </div>
          <div>
            <button type="button" id="toggle-plan-edit" hidden>Edit draft</button>
            <button type="button" id="save-plan" class="primary" hidden disabled>Save draft</button>
            <button type="button" id="approve-plan" hidden disabled>Approve plan</button>
          </div>
        </div>
        <div class="artifact-layout">
          <div id="task-markdown-files" class="log-file-list"></div>
          <div>
            <div id="task-editor-host" class="editor-host"></div>
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
    const taskModal = document.getElementById('task-modal');
    const openComposerButton = document.getElementById('open-composer');
    const closeComposerButton = document.getElementById('close-composer');
    const closeTaskModalButton = document.getElementById('close-task-modal');
    const cancelComposerButton = document.getElementById('cancel-composer');
    const requestForm = document.getElementById('request-form');
    const submitButton = document.getElementById('submit-request');
    const formError = document.getElementById('form-error');
    const formSuccess = document.getElementById('form-success');
    const targetRepoInput = document.getElementById('target_repo');
    const targetRepoOptions = document.getElementById('target-repo-options');
    const baseBranchInput = document.getElementById('base_branch');
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
    const taskLogFiles = document.getElementById('task-log-files');
    const taskLogViewer = document.getElementById('task-log-viewer');
    const taskMarkdownFiles = document.getElementById('task-markdown-files');
    const taskEditorHost = document.getElementById('task-editor-host');
    const taskEditor = document.getElementById('task-editor');
    const taskArtifactName = document.getElementById('task-artifact-name');
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
    let runningTimerHandle = null;
    let planSourceMarkdown = '';
    let planEditMode = false;
    let planEditor = null;

    targetRepoInput.value = defaultTargetRepo;
    baseBranchInput.value = defaultBaseBranch;

    async function loadBoard() {{
      const res = await fetch('/api/board');
      const data = await res.json();
      board.innerHTML = data.columns.map((column) => `
        <section class="column">
          <h2>${{column.state}}</h2>
          ${{column.items.map((item) => `<article class="card"><button class="card-button" data-task-id="${{item.task_id}}"><strong>${{item.title}}</strong><div class="card-meta">${{item.task_id}}</div><div class="card-meta">iter ${{item.iteration}}</div>${{renderRunningMeta(item)}}</button></article>`).join('')}}
        </section>`).join('');
      refreshRunningClocks();
    }}

    function isActiveState(state) {{
      return ['planning', 'implementing', 'reviewing'].includes(state);
    }}

    function renderRunningMeta(item) {{
      if (!isActiveState(item.state) || !item.state_entered_at) return '';
      return `<div class="card-meta running" data-active-since="${{item.state_entered_at}}">running 00:00:00</div>`;
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
    }}

    function syncBodyModalState() {{
      body.classList.toggle('modal-open', !modal.hidden || !taskModal.hidden);
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
      return (value || '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;');
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
    }}

    function ensurePlanEditor() {{
      if (planEditor || !window.toastui || !window.toastui.Editor) return planEditor;
      planEditor = new window.toastui.Editor({{
        el: taskEditorHost,
        height: '420px',
        initialEditType: 'wysiwyg',
        previewStyle: 'vertical',
        hideModeSwitch: false,
        usageStatistics: false,
      }});
      taskEditorHost.hidden = false;
      taskEditor.hidden = true;
      return planEditor;
    }}

    function setPlanEditorContent(value) {{
      const editor = ensurePlanEditor();
      if (editor) {{
        editor.setMarkdown(value || '');
      }} else {{
        taskEditor.hidden = false;
        taskEditorHost.hidden = true;
        taskEditor.value = value || '';
      }}
    }}

    function getPlanEditorContent() {{
      const editor = ensurePlanEditor();
      if (editor) return editor.getMarkdown();
      return taskEditor.value;
    }}

    function setPlanEditorDisabled(disabled) {{
      const editor = ensurePlanEditor();
      if (editor) {{
        if (editor.changeMode) editor.changeMode(disabled ? 'viewer' : 'wysiwyg', true);
      }}
      taskEditor.disabled = disabled;
      taskEditor.readOnly = disabled;
    }}

    function isPlanDirty() {{
      return getPlanEditorContent().replace(/\\s+$/, '') !== planSourceMarkdown.replace(/\\s+$/, '');
    }}

    function updatePlanActionState() {{
      const editableArtifact = Boolean(activeTaskDetail && activeTaskDetail.metadata.state === 'waiting-check-plans' && activeArtifactName === 'PLAN.md');
      togglePlanEditButton.hidden = !editableArtifact;
      savePlanButton.hidden = !editableArtifact;
      approvePlanButton.hidden = !editableArtifact;
      togglePlanEditButton.textContent = planEditMode ? 'Switch to viewer' : 'Edit draft';
      savePlanButton.disabled = !editableArtifact || !planEditMode;
      approvePlanButton.disabled = !editableArtifact || !planEditMode;
    }}

    function stopLogPolling() {{}}

    function renderTaskOverview(detail) {{
      const metadata = detail.metadata;
      activeTaskDetail = detail;
      const latestError = metadata.errors.length ? metadata.errors[metadata.errors.length - 1] : null;
      const viewerVisible = detail.markdown_files.length > 0;
      const planEditable = metadata.state === 'waiting-check-plans' && detail.markdown_files.includes('PLAN.md');
      taskTabEditor.hidden = !viewerVisible;
      if (!viewerVisible && taskTabEditor.classList.contains('active')) setTaskTab('overview');
      if (!activeArtifactName || !detail.markdown_files.includes(activeArtifactName)) activeArtifactName = preferredArtifact(detail.markdown_files);
      planEditMode = false;
      taskEditorStatus.textContent = planEditable ? 'View markdown artifacts here. Switch to edit mode when PLAN.md is ready for changes.' : 'Viewer mode only for this task state.';
      updatePlanActionState();
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
      if (!activeLogName || !entries.some((entry) => entry.name === activeLogName)) activeLogName = entries[0].name;
      taskLogFiles.innerHTML = entries.map((entry, index) => `<button type="button" class="${{entry.name === activeLogName ? 'active' : ''}}" data-log-index="${{index}}">${{escapeHtml(entry.name)}}</button>`).join('');
      showLogEntry(entries.findIndex((entry) => entry.name === activeLogName));
    }}

    function showLogEntry(index) {{
      const entry = activeTaskLogs[index];
      if (!entry) return;
      activeLogName = entry.name;
      taskLogViewer.textContent = entry.content || '(empty log file)';
      taskLogFiles.querySelectorAll('button').forEach((button, buttonIndex) => {{
        button.classList.toggle('active', buttonIndex === index);
      }});
    }}

    function appendRealtimeLog(eventPayload) {{
      const payload = eventPayload.payload || eventPayload;
      if (!payload || !payload.log_name || typeof payload.raw_line !== 'string') return;
      let entry = activeTaskLogs.find((item) => item.name === payload.log_name);
      if (!entry) {{
        entry = {{ name: payload.log_name, path: payload.log_name, content: '', rendered_content: '', updated_at: new Date().toISOString() }};
        activeTaskLogs = [entry, ...activeTaskLogs];
      }}
      entry.content = `${{entry.content || ''}}${{payload.raw_line}}\n`;
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
      setPlanEditorDisabled(true);
      activeArtifactName = null;
      planEditMode = false;
      savePlanButton.disabled = true;
      taskEditorStatus.textContent = 'Select a markdown artifact to view.';
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
      setPlanEditorDisabled(true);
      updatePlanActionState();
      try {{
        const response = await fetch(`/api/tasks/${{taskId}}/artifacts/${{activeArtifactName}}`);
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || `Failed to load ${{activeArtifactName}}.`);
        planSourceMarkdown = payload.content;
        setPlanEditorContent(payload.content);
        const editable = activeTaskDetail.metadata.state === 'waiting-check-plans' && activeArtifactName === 'PLAN.md' && planEditMode;
        setPlanEditorDisabled(!editable);
        updatePlanActionState();
        if (activeArtifactName === 'PLAN.md' && activeTaskDetail.metadata.state === 'waiting-check-plans') {{
          taskEditorStatus.textContent = planEditMode ? 'Editing PLAN.md. Save your draft before approval.' : 'Viewing PLAN.md. Switch to edit mode to change it.';
        }} else {{
          taskEditorStatus.textContent = `${{activeArtifactName}} is shown in viewer mode.`;
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
      setPlanEditorDisabled(!planEditMode);
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
        await loadTaskDetail(activeTaskId, true);
      }} catch (error) {{
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        taskEditorStatus.textContent = 'Approval failed.';
      }} finally {{
        updatePlanActionState();
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
    closeComposerButton.addEventListener('click', () => {{ clearMessages(); setModalOpen(false); }});
    cancelComposerButton.addEventListener('click', () => {{ clearMessages(); resetFormState(); setModalOpen(false); }});
    modal.addEventListener('click', (event) => {{ if (event.target === modal) setModalOpen(false); }});
    closeTaskModalButton.addEventListener('click', () => {{ stopLogPolling(); setTaskModalOpen(false); }});
    taskModal.addEventListener('click', (event) => {{ if (event.target === taskModal) {{ stopLogPolling(); setTaskModalOpen(false); }} }});
    document.addEventListener('keydown', (event) => {{ if (event.key === 'Escape' && !modal.hidden) setModalOpen(false); if (event.key === 'Escape' && !taskModal.hidden) {{ stopLogPolling(); setTaskModalOpen(false); }} }});
    requestForm.addEventListener('submit', submitRequest);
    board.addEventListener('click', (event) => {{ const button = event.target.closest('[data-task-id]'); if (!button) return; loadTaskDetail(button.dataset.taskId); }});
    taskTabOverview.addEventListener('click', () => setTaskTab('overview'));
    taskTabLogs.addEventListener('click', () => setTaskTab('logs'));
    taskTabEditor.addEventListener('click', () => setTaskTab('editor'));
    taskMarkdownFiles.addEventListener('click', (event) => {{ const button = event.target.closest('[data-artifact-index]'); if (!button || !activeTaskDetail) return; const file = activeTaskDetail.markdown_files[Number(button.dataset.artifactIndex)]; if (!file) return; planEditMode = false; loadMarkdownArtifact(activeTaskId, file); }});
    taskLogFiles.addEventListener('click', (event) => {{ const button = event.target.closest('[data-log-index]'); if (!button) return; showLogEntry(Number(button.dataset.logIndex)); }});
    togglePlanEditButton.addEventListener('click', togglePlanEditMode);
    savePlanButton.addEventListener('click', savePlanArtifact);
    approvePlanButton.addEventListener('click', approvePlan);
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
      if (taskModal.hidden || taskPanelLogs.hidden) return;
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
