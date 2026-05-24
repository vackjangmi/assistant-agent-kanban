    function diffMarker(kind) {
      if (kind === 'add') return '+';
      if (kind === 'remove') return '-';
      return kind === 'empty' ? '' : ' ';
    }

    function diffLineNumber(value) {
      return value == null ? '' : String(value);
    }

    function canUseLineComments() {
      return activeTaskDetail?.metadata?.state === 'human-verifying';
    }

    function lineCommentSideLabel(side) {
      return translateHumanReview(side === 'left' ? 'commentSideLeft' : 'commentSideRight');
    }

    function normalizeLineAnchor(anchor = {}) {
      return {
        path: anchor.path || activeChangedFileDetail?.summary?.path || '',
        side: anchor.side === 'left' ? 'left' : 'right',
        line_number: anchor.line_number == null ? null : Number(anchor.line_number),
        line_kind: anchor.line_kind || '',
        hunk_header: anchor.hunk_header || '',
      };
    }

    function buildLineAnchor(detail, hunk, line, side) {
      if (!detail || !hunk || !line || line.kind === 'empty' || line.line_number == null) return null;
      return normalizeLineAnchor({
        path: detail.summary.path,
        side,
        line_number: line.line_number,
        line_kind: line.kind,
        hunk_header: hunk.header,
      });
    }

    function buildLineAnchorKey(anchor) {
      const normalized = normalizeLineAnchor(anchor);
      return [normalized.path, normalized.side, normalized.line_number == null ? '' : normalized.line_number, normalized.line_kind, normalized.hunk_header].join('::');
    }

    function escapeSelectorValue(value) {
      if (window.CSS && typeof window.CSS.escape === 'function') return window.CSS.escape(value);
      return String(value).replace(/[\\"\]]/g, '\\$&');
    }

    function sameLineAnchor(left, right) {
      if (!left || !right) return false;
      return buildLineAnchorKey(left) === buildLineAnchorKey(right);
    }

    function commentThreadsByAnchor(detail) {
      const threads = new Map();
      (detail.comments || []).forEach((comment) => {
        const key = buildLineAnchorKey(comment.anchor || {});
        if (!threads.has(key)) threads.set(key, []);
        threads.get(key).push(comment);
      });
      return threads;
    }

    function formatCommentAnchor(anchor) {
      const normalized = normalizeLineAnchor(anchor);
      return translateHumanReview('commentComposerHint', {
        side: lineCommentSideLabel(normalized.side),
        lineNumber: normalized.line_number == null ? '-' : normalized.line_number,
      });
    }

    function openInlineCommentComposer(anchor) {
      if (!canUseLineComments() || !anchor) return;
      const sameAnchor = sameLineAnchor(anchor, activeInlineCommentAnchor);
      if (!sameAnchor) activeInlineCommentDraft = '';
      activeInlineCommentAnchor = normalizeLineAnchor(anchor);
      renderChangedFileDetail(activeChangedFileDetail, { preserveScroll: true, keepComposer: true, focusComposer: true });
    }

    function closeInlineCommentComposer() {
      activeInlineCommentDraft = '';
      activeInlineCommentAnchor = null;
      if (activeChangedFileDetail) renderChangedFileDetail(activeChangedFileDetail, { preserveScroll: true });
    }

    function visibleDiffContainer() {
      if (taskDiffDesktop && taskDiffDesktop.offsetParent !== null) return taskDiffDesktop;
      if (taskDiffMobile && taskDiffMobile.offsetParent !== null) return taskDiffMobile;
      return taskDiffDesktop || taskDiffMobile;
    }

    function inlineCommentElements(root = visibleDiffContainer()) {
      if (!root) return { fallback: null, submitButton: null, cancelButton: null, statusNode: null };
      return {
        fallback: root.querySelector('[data-inline-comment-fallback]'),
        submitButton: root.querySelector('[data-inline-comment-submit]'),
        cancelButton: root.querySelector('[data-inline-comment-cancel]'),
        statusNode: root.querySelector('[data-inline-comment-status]'),
      };
    }

    function syncInlineCommentDraftFromDom(root = visibleDiffContainer()) {
      const { fallback } = inlineCommentElements(root);
      if (fallback) activeInlineCommentDraft = fallback.value;
      return activeInlineCommentDraft;
    }

    function currentInlineCommentDraft() {
      return syncInlineCommentDraftFromDom();
    }

    function updateInlineCommentComposerState(status = '', tone = 'neutral', root = visibleDiffContainer()) {
      const { submitButton, cancelButton, statusNode } = inlineCommentElements(root);
      const draft = syncInlineCommentDraftFromDom(root).trim();
      if (submitButton) submitButton.disabled = taskDetailStale || !draft;
      if (cancelButton) cancelButton.disabled = false;
      if (statusNode) {
        statusNode.textContent = status;
        statusNode.dataset.tone = tone;
      }
    }

    function mountInlineCommentComposer(options = {}) {
      if (!activeInlineCommentAnchor) return;
      const { focusComposer = false } = options;
      const { fallback } = inlineCommentElements();
      if (!fallback) return;
      fallback.placeholder = translateHumanReview('commentComposerPlaceholder');
      fallback.hidden = false;
      fallback.value = activeInlineCommentDraft;
      if (focusComposer) fallback.focus();
      updateInlineCommentComposerState('', 'neutral');
    }

    function mountRenderedCommentViewers() {
      if (!activeChangedFileDetail) return;
      const commentsById = new Map((activeChangedFileDetail.comments || []).map((comment) => [String(comment.id), comment]));
      document.querySelectorAll('[data-comment-body-id]').forEach((node) => {
        const comment = commentsById.get(node.dataset.commentBodyId);
        if (!comment) return;
        if (window.toastui && window.toastui.Editor && window.toastui.Editor.factory) {
          node.innerHTML = '';
          window.toastui.Editor.factory({
            el: node,
            viewer: true,
            initialValue: comment.body_markdown || '',
          });
          return;
        }
        node.innerHTML = `<pre class="log-viewer">${escapeHtml(comment.body_markdown || '')}</pre>`;
      });
    }

    function renderCommentAction(anchor, hasComments = false) {
      if (!anchor || !canUseLineComments()) return '<span class="diff-line-action"></span>';
      const mobileLabel = currentUiLanguage() === 'KO' ? translateHumanReview('commentActionMobile') : translateHumanReview('commentAction');
      return `<span class="diff-line-action"><button type="button" class="diff-line-comment-button${hasComments ? ' has-comments' : ''}" data-line-comment-action="true" data-line-anchor-key="${escapeHtml(buildLineAnchorKey(anchor))}" aria-label="${escapeHtml(translateHumanReview('commentComposerTitle'))}">${escapeHtml(window.innerWidth <= 900 ? mobileLabel : translateHumanReview('commentAction'))}</button></span>`;
    }

    function renderCommentThread(thread = []) {
      if (!thread.length) return '';
      return `<div class="diff-thread-stack">${thread.map((comment) => `
        <article class="diff-thread-comment${comment.resolved ? ' resolved' : ''}">
          <div class="diff-thread-comment-meta">
            <div class="diff-thread-comment-meta-main">
              <strong>${escapeHtml(translateHumanReview('commentThreadLabel'))}</strong>
              ${comment.editable === false ? `<span class="diff-thread-comment-badge historical">${escapeHtml(translateHumanReview('commentHistorical'))}${comment.cycle ? ` #${escapeHtml(String(comment.cycle).padStart(3, '0'))}` : ''}</span>` : ''}
              <span>${escapeHtml(formatDateTime(comment.created_at || comment.updated_at || ''))}</span>
            </div>
            <div class="diff-thread-comment-meta-actions">
              ${comment.resolved ? `<span class="diff-thread-comment-badge">${escapeHtml(translateHumanReview('commentResolved'))}</span>` : ''}
              ${canUseLineComments() && comment.editable !== false ? `<button type="button" class="diff-thread-comment-delete" data-delete-comment-id="${escapeHtml(String(comment.id))}" ${(taskDetailStale || deletingCommentId === String(comment.id)) ? 'disabled' : ''}>${escapeHtml(deletingCommentId === String(comment.id) ? translateHumanReview('commentDeleteBusy') : translateHumanReview('commentDelete'))}</button>` : ''}
            </div>
          </div>
          <div class="diff-thread-comment-body" data-comment-body-id="${escapeHtml(String(comment.id))}"></div>
        </article>`).join('')}</div>`;
    }

    function renderInlineCommentComposer(anchor) {
      if (!anchor || !sameLineAnchor(anchor, activeInlineCommentAnchor)) return '';
      return `
        <div class="diff-inline-comment">
          <div class="diff-inline-comment-head">
            <strong>${escapeHtml(translateHumanReview('commentComposerTitle'))}</strong>
            <span class="diff-inline-comment-anchor">${escapeHtml(formatCommentAnchor(anchor))}</span>
          </div>
          <textarea class="diff-inline-comment-fallback" data-inline-comment-fallback spellcheck="true"></textarea>
          <div class="diff-inline-comment-status" data-inline-comment-status></div>
          <div class="diff-inline-comment-actions">
            <button type="button" data-inline-comment-cancel>${escapeHtml(translateHumanReview('commentComposerCancel'))}</button>
            <button type="button" class="primary" data-inline-comment-submit>${escapeHtml(translateHumanReview('commentComposerSubmit'))}</button>
          </div>
        </div>`;
    }

    function renderLineThreadSection(anchor, threads) {
      if (!anchor) return '';
      const anchorKey = buildLineAnchorKey(anchor);
      const thread = threads.get(anchorKey) || [];
      const threadHtml = renderCommentThread(thread);
      const composerHtml = renderInlineCommentComposer(anchor);
      if (!threadHtml && !composerHtml) return '';
      return `<div class="diff-line-thread-shell" data-thread-anchor-key="${escapeHtml(anchorKey)}">${threadHtml}${composerHtml}</div>`;
    }

    function renderDesktopDiffCell(detail, hunk, line, side, threads) {
      const anchor = buildLineAnchor(detail, hunk, line, side);
      const anchorKey = anchor ? buildLineAnchorKey(anchor) : '';
      const thread = anchorKey ? (threads.get(anchorKey) || []) : [];
      return `
        <div class="diff-cell ${line.kind}"${anchorKey ? ` data-line-anchor-key="${escapeHtml(anchorKey)}"` : ''}>
          <span class="diff-line-number">${escapeHtml(diffLineNumber(line.line_number))}</span>
          ${renderCommentAction(anchor, thread.length > 0)}
          <span class="diff-marker">${escapeHtml(diffMarker(line.kind))}</span>
          <span class="diff-content">${escapeHtml(line.content || ' ')}</span>
          ${renderLineThreadSection(anchor, threads)}
        </div>`;
    }

    function mobileRowEntries(detail, hunk, row) {
      const leftAnchor = buildLineAnchor(detail, hunk, row.left, 'left');
      const rightAnchor = buildLineAnchor(detail, hunk, row.right, 'right');
      if (row.left.kind === 'context' && row.right.kind === 'context' && row.left.content === row.right.content) {
        return [{ line: row.right, side: 'right', anchor: rightAnchor }];
      }
      const entries = [];
      if (row.left.kind !== 'empty' && leftAnchor) entries.push({ line: row.left, side: 'left', anchor: leftAnchor });
      if (row.right.kind !== 'empty' && rightAnchor) entries.push({ line: row.right, side: 'right', anchor: rightAnchor });
      return entries;
    }

    function renderMobileDiffLine(entry, threads) {
      const anchorKey = entry.anchor ? buildLineAnchorKey(entry.anchor) : '';
      const thread = anchorKey ? (threads.get(anchorKey) || []) : [];
      return `
        <div class="diff-mobile-line ${entry.line.kind}"${anchorKey ? ` data-line-anchor-key="${escapeHtml(anchorKey)}"` : ''}>
          <div class="diff-mobile-line-head">
            <span class="diff-mobile-line-side">${escapeHtml(lineCommentSideLabel(entry.side))}</span>
            <span class="diff-line-number">${escapeHtml(diffLineNumber(entry.line.line_number))}</span>
            ${renderCommentAction(entry.anchor, thread.length > 0)}
            <span class="diff-marker">${escapeHtml(diffMarker(entry.line.kind))}</span>
            <span class="diff-content">${escapeHtml(entry.line.content || ' ')}</span>
          </div>
          ${renderLineThreadSection(entry.anchor, threads)}
        </div>`;
    }

    function renderChangedFileButtons(files) {
      if (!files.length) {
        taskChangedFiles.innerHTML = `<div class="muted">${escapeHtml(translateTask('noChangedFilesCaptured'))}</div>`;
        return;
      }
      taskChangedFiles.innerHTML = files.map((file) => `
        <div class="diff-file-row">
          <button type="button" class="${file.id === activeChangedFileId ? 'active' : ''}${file.viewed ? ' is-viewed' : ''}" data-changed-file-id="${escapeHtml(file.id)}">
            ${renderChangedFilePathHeading(file.display_path)}
            <span class="diff-file-meta">
              ${file.viewed ? `<span class="diff-badge diff-badge-viewed">${escapeHtml(translateHumanReview('viewedBadge'))}</span>` : ''}
              <span>${escapeHtml(file.change_type)}</span>
              <span>+${file.additions}</span>
              <span>-${file.deletions}</span>
            </span>
          </button>
        </div>
      `).join('');
    }

    function qaChecklistItems() {
      const items = activeTaskDetail?.human_review?.qa_items;
      return Array.isArray(items) ? items : [];
    }

    function qaChecklistProgress() {
      const items = qaChecklistItems();
      const requiredItems = items.filter((item) => item.required);
      const completedRequired = requiredItems.filter((item) => item.checked || item.skipped);
      return { total: items.length, required: requiredItems.length, completedRequired: completedRequired.length };
    }

    let qaChecklistPendingScrollState = null;

    function setApprovalGateNotice({ title = '', body = '', actionLabel = '', action = '' } = {}) {
      if (!taskApprovalGateNotice) return;
      if (!title && !body) {
        taskApprovalGateNotice.hidden = true;
        taskApprovalGateNotice.innerHTML = '';
        return;
      }
      taskApprovalGateNotice.hidden = false;
      taskApprovalGateNotice.innerHTML = `
        <div class="approval-gate-copy">
          <strong>${escapeHtml(title)}</strong>
          <span>${escapeHtml(body)}</span>
        </div>
        ${actionLabel && action ? `<button type="button" class="accent-button" data-approval-gate-action="${escapeHtml(action)}">${escapeHtml(actionLabel)}</button>` : ''}
      `;
    }

    function qaChecklistScrollElements() {
      const elements = [
        taskQaChecklistItems,
        taskPanelQaChecklist,
        taskQaChecklistPanel,
        taskModal.querySelector('.task-modal-panel'),
        document.scrollingElement,
      ].filter(Boolean);
      return elements.filter((element, index) => elements.indexOf(element) === index);
    }

    function captureQaChecklistScrollState() {
      return qaChecklistScrollElements().map((element) => ({
        element,
        scrollTop: element.scrollTop,
        scrollLeft: element.scrollLeft,
      }));
    }

    function rememberQaChecklistScrollState() {
      qaChecklistPendingScrollState = captureQaChecklistScrollState();
    }

    function consumeQaChecklistScrollState() {
      const state = qaChecklistPendingScrollState || captureQaChecklistScrollState();
      qaChecklistPendingScrollState = null;
      return state;
    }

    function restoreQaChecklistScrollState(state) {
      if (!Array.isArray(state)) return;
      state.forEach((item) => {
        if (!item?.element) return;
        item.element.scrollTop = item.scrollTop;
        item.element.scrollLeft = item.scrollLeft;
      });
    }

    function scheduleQaChecklistScrollRestore(state) {
      if (!Array.isArray(state)) return;
      restoreQaChecklistScrollState(state);
      requestAnimationFrame(() => {
        restoreQaChecklistScrollState(state);
        requestAnimationFrame(() => restoreQaChecklistScrollState(state));
      });
    }

    function renderQaChecklistPanel({ preserveScroll = false, scrollState = null } = {}) {
      const state = activeTaskDetail?.metadata?.state || '';
      const canToggle = state === 'human-verifying' && !taskDetailStale;
      const items = qaChecklistItems();
      const progress = qaChecklistProgress();
      const nextScrollState = scrollState || (preserveScroll ? captureQaChecklistScrollState() : null);
      taskQaChecklistPanel.hidden = !(state === 'completed-reviews' || state === 'human-verifying');
      taskQaChecklistStatus.textContent = state === 'human-verifying'
        ? translateHumanReview('qaChecklistInteractive')
        : translateHumanReview('qaChecklistReadOnly');
      taskQaChecklistBadges.innerHTML = `
        <span class="diff-badge">${escapeHtml(translateHumanReview('qaChecklistProgress', { completed: progress.completedRequired, required: progress.required }))}</span>
        ${activeTaskDetail?.human_review?.qa_path ? `<span class="diff-badge">${escapeHtml(activeTaskDetail.human_review.qa_path)}</span>` : ''}
      `;
      if (!items.length) {
        taskQaChecklistItems.innerHTML = `<div class="diff-empty">${escapeHtml(translateHumanReview('qaChecklistEmpty'))}</div>`;
        scheduleQaChecklistScrollRestore(nextScrollState);
        return;
      }
      taskQaChecklistItems.innerHTML = items.map((item) => {
        const complete = Boolean(item.checked || item.skipped);
        const steps = Array.isArray(item.steps) ? item.steps : [];
        return `
          <article class="qa-checklist-item${complete ? ' is-complete' : ''}" data-qa-item-id="${escapeHtml(item.id)}">
            <div class="qa-checklist-item-head">
              <div class="qa-checklist-title">
                <strong>${escapeHtml(item.title || item.id)}</strong>
                <div class="diff-badges">
                  <span class="diff-badge" data-qa-badge-type="${item.required ? 'required' : 'optional'}">${escapeHtml(item.required ? translateHumanReview('qaChecklistRequired') : translateHumanReview('qaChecklistOptional'))}</span>
                  <span class="diff-badge" data-qa-badge-type="id">${escapeHtml(item.id)}</span>
                </div>
              </div>
              <div class="qa-checklist-actions">
                <label class="qa-checklist-toggle${item.checked ? ' is-active' : ''}" ${canToggle ? '' : 'aria-disabled="true"'}>
                  <input type="checkbox" data-qa-check="${escapeHtml(item.id)}" ${item.checked ? 'checked' : ''} ${canToggle ? '' : 'disabled'}>
                  <span>${escapeHtml(translateHumanReview('qaChecklistChecked'))}</span>
                </label>
                <label class="qa-checklist-toggle${item.skipped ? ' is-skipped' : ''}" ${canToggle ? '' : 'aria-disabled="true"'}>
                  <input type="checkbox" data-qa-skip="${escapeHtml(item.id)}" ${item.skipped ? 'checked' : ''} ${canToggle ? '' : 'disabled'}>
                  <span>${escapeHtml(translateHumanReview('qaChecklistSkipped'))}</span>
                </label>
              </div>
            </div>
            <ol class="qa-checklist-steps">${steps.map((step) => `<li>${escapeHtml(step)}</li>`).join('')}</ol>
            <div class="qa-checklist-expected"><strong>${escapeHtml(translateHumanReview('qaChecklistExpected'))}:</strong> ${escapeHtml(item.expected_result || '')}</div>
            <input class="qa-checklist-note" data-qa-note="${escapeHtml(item.id)}" value="${escapeHtml(item.note || '')}" placeholder="${escapeHtml(translateHumanReview('qaChecklistNotePlaceholder'))}" ${canToggle ? '' : 'disabled'}>
          </article>`;
      }).join('');
      scheduleQaChecklistScrollRestore(nextScrollState);
    }

    function applyQaChecklistItemUpdate(updated, { scrollState = null } = {}) {
      if (!updated || !activeTaskDetail?.human_review?.qa_items) return;
      activeTaskDetail.human_review.qa_items = activeTaskDetail.human_review.qa_items.map((item) => item.id === updated.id ? { ...item, ...updated } : item);
      const progress = qaChecklistProgress();
      activeTaskDetail.human_review.qa_total_count = progress.total;
      activeTaskDetail.human_review.qa_required_count = progress.required;
      activeTaskDetail.human_review.qa_completed_required_count = progress.completedRequired;
      const nextScrollState = scrollState || captureQaChecklistScrollState();
      renderQaChecklistPanel({ scrollState: nextScrollState });
      updateHumanReviewPanel();
      scheduleQaChecklistScrollRestore(nextScrollState);
    }

    async function setQaChecklistItemState(taskId, itemId, patch, options = {}) {
      const response = await fetch(`/api/tasks/${taskId}/human-qa/${encodeURIComponent(itemId)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || translateHumanReview('qaChecklistSaveError'));
      applyQaChecklistItemUpdate(payload, options);
      return payload;
    }

    function renderChangedFileSummaryCard(summary) {
      const commentCount = Array.isArray(activeChangedFileDetail?.comments) ? activeChangedFileDetail.comments.length : 0;
      const historicalCount = commentCount ? activeChangedFileDetail.comments.filter((comment) => comment.editable === false).length : 0;
      const threadCount = commentCount ? new Set((activeChangedFileDetail.comments || []).map((comment) => buildLineAnchorKey(comment.anchor || {}))).size : 0;
      const headingMarkup = renderChangedFilePathHeading(summary.display_path, 'diff-summary-copy');
      const threadBadge = threadCount
        ? `<span class="diff-badge">${escapeHtml(translateHumanReview(threadCount === 1 ? 'commentsExistingOne' : 'commentsExistingMany', { count: threadCount }))}</span>`
        : '';
      const historyBadge = historicalCount
        ? `<span class="diff-badge">${escapeHtml(translateHumanReview('commentHistorical'))}</span>`
        : '';
      const canToggleViewed = activeTaskDetail?.metadata?.state === 'human-verifying';
      const viewedToggle = `
        <label class="diff-file-viewed-toggle${summary.viewed ? ' is-viewed' : ''}" ${canToggleViewed ? '' : 'aria-disabled="true"'}>
          <input type="checkbox" data-viewed-changed-file-id="${escapeHtml(summary.id)}" ${summary.viewed ? 'checked' : ''} ${canToggleViewed ? '' : 'disabled'}>
          <span>${escapeHtml(translateHumanReview('viewedLabel'))}</span>
        </label>`;
      taskChangedFileSummary.innerHTML = `
        <div class="diff-summary-main">
          ${headingMarkup}
          <div class="diff-summary-side">
            ${viewedToggle}
            <div class="diff-badges">
              <span class="diff-badge">${escapeHtml(summary.change_type)}</span>
              <span class="diff-badge">+${summary.additions}</span>
              <span class="diff-badge">-${summary.deletions}</span>
              ${threadBadge}
              ${historyBadge}
            </div>
          </div>
        </div>
      `;
    }

    function splitDisplayPath(displayPath) {
      const normalizedPath = typeof displayPath === 'string' ? displayPath.trim() : '';
      if (!normalizedPath) {
        return { filename: '', directory: '' };
      }
      const lastSlashIndex = normalizedPath.lastIndexOf('/');
      if (lastSlashIndex === -1) {
        return { filename: normalizedPath, directory: '' };
      }
      return {
        filename: normalizedPath.slice(lastSlashIndex + 1),
        directory: normalizedPath.slice(0, lastSlashIndex + 1),
      };
    }

    function renderChangedFilePathHeading(displayPath, containerClass = 'diff-file-heading') {
      const { filename, directory } = splitDisplayPath(displayPath);
      const safeFilename = escapeHtml(filename || displayPath || '');
      const safeDirectory = directory ? `<span class="diff-file-path">${escapeHtml(directory)}</span>` : '';
      return `
        <span class="${escapeHtml(containerClass)}">
          <span class="diff-file-title">${safeFilename}</span>
          ${safeDirectory}
        </span>`;
    }

    function renderDiffPlaceholder(message) {
      taskDiffDesktop.innerHTML = `<div class="diff-empty">${escapeHtml(message)}</div>`;
      taskDiffMobile.innerHTML = `<div class="diff-empty">${escapeHtml(message)}</div>`;
    }

    function buildUnifiedDiffLines(detail) {
      if (detail.summary.is_binary) {
        return null;
      }
      if (!detail.hunks.length) {
        return [];
      }
      const lines = [
        `diff --git a/${detail.summary.path} b/${detail.summary.path}`,
        detail.summary.change_type === 'added' ? 'new file mode 100644' : null,
        detail.summary.change_type === 'removed' ? 'deleted file mode 100644' : null,
        `--- ${detail.summary.change_type === 'added' ? '/dev/null' : `a/${detail.summary.path}`}`,
        `+++ ${detail.summary.change_type === 'removed' ? '/dev/null' : `b/${detail.summary.path}`}`,
      ].filter(Boolean);
      detail.hunks.forEach((hunk) => {
        lines.push(hunk.header);
        hunk.unified_lines.forEach((line) => {
          const marker = diffMarker(line.kind);
          lines.push(`${marker}${line.content || ''}`);
        });
      });
      return lines;
    }

    function renderUnifiedDiff(detail) {
      const lines = buildUnifiedDiffLines(detail);
      if (lines === null) {
        return `<div class="diff-empty">${escapeHtml(translateTask('noBinaryPreview'))}</div>`;
      }
      if (!lines.length) {
        return `<div class="diff-empty">${escapeHtml(translateTask('noTextHunks'))}</div>`;
      }
      return `<pre class="diff-unified">${lines.map((line) => {
        let kind = 'context';
        if (line.startsWith('@@') || line.startsWith('diff --git') || line.startsWith('--- ') || line.startsWith('+++ ') || line.endsWith('mode 100644')) kind = 'header';
        else if (line.startsWith('+')) kind = 'add';
        else if (line.startsWith('-')) kind = 'remove';
        return `<span class="diff-unified-line ${kind}">${escapeHtml(line)}</span>`;
      }).join('')}</pre>`;
    }

    function renderDiffDesktop(detail) {
      if (detail.summary.is_binary) {
        return `<div class="diff-empty">${escapeHtml(translateTask('noBinaryPreview'))}</div>`;
      }
      if (!detail.hunks.length) {
        return `<div class="diff-empty">${escapeHtml(translateTask('noTextHunks'))}</div>`;
      }
      const threads = commentThreadsByAnchor(detail);
      return detail.hunks.map((hunk) => `
        <section class="diff-hunk">
          <div class="diff-hunk-header">${escapeHtml(hunk.header)}</div>
          <div class="diff-grid">${hunk.rows.map((row) => `
            <div class="diff-row">
              ${renderDesktopDiffCell(detail, hunk, row.left, 'left', threads)}
              ${renderDesktopDiffCell(detail, hunk, row.right, 'right', threads)}
            </div>
          `).join('')}</div>
        </section>
      `).join('');
    }

    function renderDiffMobile(detail) {
      if (detail.summary.is_binary) {
        return `<div class="diff-empty">${escapeHtml(translateTask('noBinaryPreview'))}</div>`;
      }
      if (!detail.hunks.length) {
        return `<div class="diff-empty">${escapeHtml(translateTask('noTextHunks'))}</div>`;
      }
      const threads = commentThreadsByAnchor(detail);
      return detail.hunks.map((hunk) => `
        <section class="diff-hunk">
          <div class="diff-hunk-header">${escapeHtml(hunk.header)}</div>
          <div class="diff-mobile-stack">${hunk.rows.map((row) => mobileRowEntries(detail, hunk, row).map((entry) => renderMobileDiffLine(entry, threads)).join('')).join('')}</div>
        </section>
      `).join('');
    }

    function findDiffAnchorNode(container, anchorKey) {
      if (!container || !anchorKey) return null;
      return container.querySelector(`[data-thread-anchor-key="${escapeSelectorValue(anchorKey)}"]`) || container.querySelector(`[data-line-anchor-key="${escapeSelectorValue(anchorKey)}"]`);
    }

    function captureDiffAnchorState(container, anchorKey) {
      const node = findDiffAnchorNode(container, anchorKey);
      if (!node) return null;
      const containerRect = container.getBoundingClientRect();
      const nodeRect = node.getBoundingClientRect();
      return {
        offsetTop: nodeRect.top - containerRect.top,
      };
    }

    function restoreDiffAnchorState(container, anchorKey, state) {
      if (!container || !anchorKey || !state) return false;
      const node = findDiffAnchorNode(container, anchorKey);
      if (!node) return false;
      const containerRect = container.getBoundingClientRect();
      const nodeRect = node.getBoundingClientRect();
      container.scrollTop += (nodeRect.top - containerRect.top) - state.offsetTop;
      return true;
    }

    function renderChangedFileDetail(detail, options = {}) {
      const { preserveScroll = false, keepComposer = false, scrollAnchor = null, focusComposer = false } = options;
      if (activeInlineCommentAnchor) syncInlineCommentDraftFromDom();
      const desktopScrollTop = taskDiffDesktop.scrollTop;
      const mobileScrollTop = taskDiffMobile.scrollTop;
      const visibleContainerBeforeRender = visibleDiffContainer();
      const anchorKey = scrollAnchor ? buildLineAnchorKey(scrollAnchor) : '';
      const visibleAnchorState = anchorKey ? captureDiffAnchorState(visibleContainerBeforeRender, anchorKey) : null;
      activeChangedFileDetail = detail;
      if (!keepComposer && activeInlineCommentAnchor && !commentThreadsByAnchor(detail).has(buildLineAnchorKey(activeInlineCommentAnchor))) {
        activeInlineCommentDraft = '';
        activeInlineCommentAnchor = null;
      }
      renderChangedFileSummaryCard(detail.summary);
      taskDiffDesktop.innerHTML = renderDiffDesktop(detail);
      taskDiffMobile.innerHTML = renderDiffMobile(detail);
      mountRenderedCommentViewers();
      mountInlineCommentComposer({ focusComposer });
      if (preserveScroll) {
        const visibleContainerAfterRender = visibleDiffContainer();
        const restoredVisible = restoreDiffAnchorState(visibleContainerAfterRender, anchorKey, visibleAnchorState);
        if (visibleContainerAfterRender === taskDiffDesktop) {
          if (!restoredVisible) taskDiffDesktop.scrollTop = desktopScrollTop;
          taskDiffMobile.scrollTop = mobileScrollTop;
        } else {
          if (!restoredVisible) taskDiffMobile.scrollTop = mobileScrollTop;
          taskDiffDesktop.scrollTop = desktopScrollTop;
        }
      }
      updateHumanReviewPanel();
    }
