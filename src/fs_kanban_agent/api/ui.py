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
    .card-meta {{ color: var(--muted); font-size: 0.95rem; }}
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
    @media (max-width: 900px) {{ #board, .composer-grid {{ grid-template-columns: 1fr; }} .modal {{ padding: 12px; align-items: stretch; }} .modal-panel {{ max-height: none; }} .form-actions {{ flex-direction: column-reverse; }} .form-actions button {{ width: 100%; }} }}
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
  <main id="board"></main>
  <script>
    const board = document.getElementById('board');
    const body = document.body;
    const modal = document.getElementById('request-modal');
    const openComposerButton = document.getElementById('open-composer');
    const closeComposerButton = document.getElementById('close-composer');
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
    let lastAutoScope = '';
    let lastAutoOutOfScope = '';

    targetRepoInput.value = defaultTargetRepo;
    baseBranchInput.value = defaultBaseBranch;

    async function loadBoard() {{
      const res = await fetch('/api/board');
      const data = await res.json();
      board.innerHTML = data.columns.map((column) => `
        <section class="column">
          <h2>${{column.state}}</h2>
          ${{column.items.map((item) => `<article class="card"><strong>${{item.title}}</strong><div class="card-meta">${{item.task_id}}</div><div class="card-meta">iter ${{item.iteration}}</div></article>`).join('')}}
        </section>`).join('');
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
      body.classList.toggle('modal-open', isOpen);
      if (isOpen) document.getElementById('title').focus();
    }}

    function clearMessages() {{
      formError.hidden = true;
      formError.textContent = '';
      formSuccess.hidden = true;
      formSuccess.textContent = '';
      document.querySelectorAll('[data-error-for]').forEach((node) => {{ node.textContent = ''; }});
    }}

    function normalizeRepoPath(value) {{
      return (value || '').toString().trim().replace(/\/+$/, '');
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
        `Limit code changes to \`${{context.normalized}}\`.`,
        `Modify only the files under \`${{context.normalized}}\` that are needed for this request.`,
        `Keep tests and local configuration changes scoped to \`${{context.normalized}}\`.`,
      ];
      if (context.repoName && context.repoName !== 'target repo') lines.push(`Focus on the \`${{context.repoName}}\` project or app.`);
      return lines.join('\\n');
    }}

    function buildOutOfScopeDefaults(path) {{
      const context = deriveRepoContext(path);
      const lines = [
        `Do not modify files outside \`${{context.normalized}}\`.`,
        'Do not change unrelated apps, packages, or workspace-wide configuration.',
        'Do not add deployment or infrastructure changes unless the request explicitly asks for them.',
      ];
      if (context.parentName) lines.push(`Do not modify sibling projects under \`${{context.parentName}}/\` unless the request explicitly requires it.`);
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
    document.addEventListener('keydown', (event) => {{ if (event.key === 'Escape' && !modal.hidden) setModalOpen(false); }});
    requestForm.addEventListener('submit', submitRequest);
    ['title', 'goal', 'target_repo', 'base_branch'].forEach((name) => {{ requestForm.elements[name].addEventListener('blur', validateForm); }});
    targetRepoInput.addEventListener('input', applyRepoDefaults);
    targetRepoInput.addEventListener('change', applyRepoDefaults);
    scopeField.addEventListener('input', () => {{ scopeField.dataset.autofilled = 'false'; }});
    outOfScopeField.addEventListener('input', () => {{ outOfScopeField.dataset.autofilled = 'false'; }});
    resetFormState();
    loadTargetRepoOptions();
    const source = new EventSource('/api/events');
    source.addEventListener('board_snapshot', loadBoard);
    loadBoard();
  </script>
</body>
</html>
"""

    return router
