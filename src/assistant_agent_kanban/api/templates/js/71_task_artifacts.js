    function preferredArtifact(files, metadata = activeTaskDetail?.metadata) {
      const currentHumanReviewNote = activeTaskDetail?.human_review?.note_path || metadata?.human_verification?.note_path || null;
      if (metadata?.state === 'human-verifying' && currentHumanReviewNote && files.includes(currentHumanReviewNote)) return currentHumanReviewNote;
      if (['plan-approving', 'waiting-check-plans', 'completed-reviews', 'human-verifying', 'done'].includes(metadata?.state) && files.includes('PLAN.md')) return 'PLAN.md';
      return files[0] || null;
    }

    function artifactDisplayLabel(file) {
      return file.replace(/\.md$/, '');
    }

    function parseArtifactCycle(file) {
      const match = /^(WORK|REVIEW|HUMAN-QA|HUMAN-VERIFY)-([0-9]{3})\.md$/.exec(file);
      if (!match) {
        const reviewerQaMatch = /^(REVIEWER-QA)-([0-9]{3})\.md$/.exec(file);
        if (!reviewerQaMatch) return null;
        return { kind: reviewerQaMatch[1], cycle: reviewerQaMatch[2] };
      }
      return { kind: match[1], cycle: match[2] };
    }

    function formatArtifactFamilyLabel(cycle) {
      return currentUiLanguage() === 'KO' ? `구현&리뷰-${cycle}` : `Implement&Review-${cycle}`;
    }

    function buildArtifactEntries(files) {
      const entries = [];
      let currentCycleEntry = null;
      files.forEach((file, index) => {
        const cycleInfo = parseArtifactCycle(file);
        if (cycleInfo) {
          if (!currentCycleEntry || currentCycleEntry.cycle !== cycleInfo.cycle) {
            currentCycleEntry = {
              type: 'family',
              cycle: cycleInfo.cycle,
              key: `cycle-${cycleInfo.cycle}`,
              label: formatArtifactFamilyLabel(cycleInfo.cycle),
              files: [],
            };
            entries.push(currentCycleEntry);
          }
          currentCycleEntry.files.push({ file, index, kind: cycleInfo.kind });
          return;
        }
        currentCycleEntry = null;
        entries.push({ type: 'file', file, index, label: artifactDisplayLabel(file) });
      });
      return entries;
    }

    function activeArtifactFamily(entries) {
      return entries.find((entry) => entry.type === 'family' && entry.files.some((item) => item.file === activeArtifactName)) || null;
    }

    function renderArtifactSubtabs(entries) {
      const family = activeArtifactFamily(entries);
      if (!family) {
        taskArtifactSubtabs.hidden = true;
        taskArtifactSubtabs.innerHTML = '';
        return;
      }
      taskArtifactSubtabs.hidden = false;
      taskArtifactSubtabs.innerHTML = family.files.map((item) => `<button type="button" class="${item.file === activeArtifactName ? 'active' : ''}" data-artifact-file="${escapeHtml(item.file)}">${escapeHtml(item.kind)}</button>`).join('');
    }

    function renderArtifactButtons(files) {
      if (!files.length) {
        taskMarkdownFiles.innerHTML = `<div class="muted">${escapeHtml(translateTask('noMarkdownArtifactsYet'))}</div>`;
        taskArtifactSubtabs.hidden = true;
        taskArtifactSubtabs.innerHTML = '';
        taskArtifactName.textContent = translateTask('noDocumentSelected');
        return;
      }
      const entries = buildArtifactEntries(files);
      taskMarkdownFiles.innerHTML = entries.map((entry) => {
        if (entry.type === 'family') {
          const active = entry.files.some((item) => item.file === activeArtifactName);
          const defaultFile = active ? activeArtifactName : (entry.files[0]?.file || '');
          return `<button type="button" class="${active ? 'active' : ''}" data-artifact-file="${escapeHtml(defaultFile)}">${escapeHtml(entry.label)}</button>`;
        }
        return `<button type="button" class="${entry.file === activeArtifactName ? 'active' : ''}" data-artifact-file="${escapeHtml(entry.file)}">${escapeHtml(entry.label)}</button>`;
      }).join('');
      renderArtifactSubtabs(entries);
      taskArtifactName.textContent = activeArtifactName || translateTask('noDocumentSelected');
    }

    function renderTaskLogButtons(entries) {
      if (!entries.length) {
        taskLogFiles.innerHTML = `<div class="muted">${escapeHtml(translateTask('runtimeLogSummaryEmpty'))}</div>`;
        return;
      }
      taskLogFiles.innerHTML = entries.map((entry) => `<button type="button" class="${entry.name === activeLogName ? 'active' : ''}" data-log-name="${escapeHtml(entry.name)}">${escapeHtml(entry.name)}</button>`).join('');
    }

    function renderTaskLogs(logs, { preserveSelection = true } = {}) {
      const scrollState = preserveSelection ? captureTaskLogScrollState() : null;
      activeTaskLogs = logs;
      const entries = Array.isArray(logs?.entries) ? logs.entries : [];
      const fallbackLogName = entries[entries.length - 1]?.name || null;
      const hasCurrentSelection = preserveSelection && activeLogName && entries.some((entry) => entry.name === activeLogName);
      activeLogName = hasCurrentSelection ? activeLogName : fallbackLogName;
      renderTaskLogButtons(entries);
      if (!entries.length || !activeLogName) {
        taskLogName.textContent = translateTask('noLogSelected');
        taskLogStatus.textContent = translateTask('runtimeLogSummaryEmpty');
        taskLogViewer.textContent = translateTask('runtimeLogSummaryEmpty');
        return;
      }
      const activeEntry = entries.find((entry) => entry.name === activeLogName) || entries[entries.length - 1];
      activeLogName = activeEntry?.name || null;
      taskLogName.textContent = activeEntry?.name || translateTask('noLogSelected');
      taskLogStatus.textContent = activeEntry ? translateTask('viewingLog', { name: activeEntry.name }) : translateTask('selectRuntimeLog');
      taskLogViewer.textContent = activeEntry ? displayTaskLogContent(activeEntry) : translateTask('selectRuntimeLog');
      if (scrollState) {
        restoreTaskLogScrollState(scrollState);
      }
    }

    function displayTaskLogContent(entry) {
      if (!entry) return translateTask('runtimeLogUnavailable');
      const rendered = (entry.rendered_content || '').trim();
      const debug = (entry.debug_rendered_content || '').trim();
      if (rendered && debug && rendered !== debug) {
        return `${rendered}\n\n----- Debug / Thinking -----\n\n${debug}`;
      }
      return rendered || debug || translateTask('runtimeLogUnavailable');
    }

    function mergeTaskLogSnapshot(previous, incoming) {
      return {
        ...previous,
        ...incoming,
        rendered_content: incoming.rendered_content ?? previous.rendered_content ?? null,
        debug_rendered_content: incoming.debug_rendered_content ?? previous.debug_rendered_content ?? null,
      };
    }

    function appendTaskLogDelta(renderedDelta, debugDelta) {
      const hasRenderedDelta = typeof renderedDelta === 'string' && renderedDelta.length > 0;
      const hasDebugDelta = typeof debugDelta === 'string' && debugDelta.length > 0;
      if (!hasRenderedDelta && !hasDebugDelta) return false;
      const scrollState = captureTaskLogScrollState();
      const currentText = taskLogViewer.textContent || '';
      if (hasRenderedDelta && hasDebugDelta && renderedDelta.trim() !== debugDelta.trim()) {
        const separator = currentText.includes('----- Debug / Thinking -----')
          ? ''
          : `${currentText ? '\n\n' : ''}----- Debug / Thinking -----\n\n`;
        taskLogViewer.textContent = `${currentText}${hasRenderedDelta ? renderedDelta : ''}${separator}${debugDelta}`;
      } else {
        taskLogViewer.textContent = `${currentText}${hasRenderedDelta ? renderedDelta : debugDelta}`;
      }
      restoreTaskLogScrollState(scrollState);
      return true;
    }

    function captureTaskLogScrollState() {
      const maxScrollTop = Math.max(0, taskLogViewer.scrollHeight - taskLogViewer.clientHeight);
      return {
        scrollTop: taskLogViewer.scrollTop,
        scrollHeight: taskLogViewer.scrollHeight,
        hadScrollableOverflow: maxScrollTop > 0,
        wasNearBottom: taskLogViewerPinnedToBottom || maxScrollTop - taskLogViewer.scrollTop <= 24,
      };
    }

    function restoreTaskLogScrollState(state) {
      if (!state) return;
      const nextMax = Math.max(0, taskLogViewer.scrollHeight - taskLogViewer.clientHeight);
      if (state.wasNearBottom || (!state.hadScrollableOverflow && nextMax > 0)) {
        taskLogViewer.scrollTop = taskLogViewer.scrollHeight;
        taskLogViewerPinnedToBottom = true;
        return;
      }
      taskLogViewer.scrollTop = state.scrollTop;
      taskLogViewerPinnedToBottom = false;
    }


    function scrollTaskLogViewerToBottom() {
      taskLogViewer.scrollTop = taskLogViewer.scrollHeight;
      taskLogViewerPinnedToBottom = true;
    }

    function updateTaskLogViewerPinnedToBottom() {
      const maxScrollTop = Math.max(0, taskLogViewer.scrollHeight - taskLogViewer.clientHeight);
      taskLogViewerPinnedToBottom = maxScrollTop - taskLogViewer.scrollTop <= 24;
    }

    function updateTaskLogViewerContent(previousContent, nextContent) {
      const scrollState = captureTaskLogScrollState();
      const prior = previousContent || '';
      const next = nextContent || translateTask('runtimeLogUnavailable');
      if (prior && next.startsWith(prior)) {
        const suffix = next.slice(prior.length);
        if (suffix) taskLogViewer.textContent += suffix;
        restoreTaskLogScrollState(scrollState);
        return;
      }
      taskLogViewer.textContent = next;
      restoreTaskLogScrollState(scrollState);
    }

    function appendWorkerLogPayload(payload) {
      if (!payload || !activeTaskLogs || !payload.log_name) return false;
      const entries = Array.isArray(activeTaskLogs.entries) ? activeTaskLogs.entries : [];
      activeTaskLogs.entries = entries;
      const existingIndex = entries.findIndex((entry) => entry.name === payload.log_name);
      const previousEntry = existingIndex >= 0 ? entries[existingIndex] : null;
      const nextEntry = mergeTaskLogSnapshot(previousEntry || {
        name: payload.log_name,
        path: '',
        rendered_content: null,
        debug_rendered_content: null,
        updated_at: new Date().toISOString(),
      }, {
        name: payload.log_name,
        rendered_content: payload.rendered_content ?? null,
        debug_rendered_content: payload.debug_rendered_content ?? null,
        updated_at: new Date().toISOString(),
      });
      if (existingIndex >= 0) entries[existingIndex] = nextEntry;
      else entries.push(nextEntry);
      const shouldSelectNewEntry = !activeLogName;
      if (shouldSelectNewEntry) activeLogName = payload.log_name;
      if (existingIndex < 0 || shouldSelectNewEntry) renderTaskLogButtons(entries);
      if (activeLogName !== payload.log_name) return true;
      if (appendTaskLogDelta(payload.rendered_delta, payload.debug_rendered_delta)) {
        taskLogName.textContent = nextEntry.name || translateTask('noLogSelected');
        taskLogStatus.textContent = translateTask('viewingLog', { name: nextEntry.name || translateTask('noLogSelected') });
        return true;
      }
      const previousContent = displayTaskLogContent(previousEntry);
      const nextContent = displayTaskLogContent(nextEntry);
      taskLogName.textContent = nextEntry.name || translateTask('noLogSelected');
      taskLogStatus.textContent = translateTask('viewingLog', { name: nextEntry.name || translateTask('noLogSelected') });
      updateTaskLogViewerContent(previousContent, nextContent);
      return true;
    }

    function appendReviewerQaWorkerLogPayload(payload) {
      if (!payload || payload.log_name !== 'reviewer-qa.jsonl') return false;
      if (!reviewerQaQuestionInFlight && !reviewerQaPendingQuestion) return false;
      const renderedContent = typeof payload.rendered_content === 'string' ? payload.rendered_content : '';
      const renderedDelta = typeof payload.rendered_delta === 'string' ? payload.rendered_delta : '';
      const nextAnswer = renderedContent ? renderedContent.trim() : `${reviewerQaPendingAnswer}${renderedDelta}`.trim();
      if (!nextAnswer) return true;
      reviewerQaPendingAnswer = nextAnswer;
      setReviewerQaTranscript(reviewerQaSourceMarkdown, { preserveScroll: true });
      updateReviewerQaPanel();
      return true;
    }

    function appendWorkerLogFilePayload(payload) {
      if (!payload || !activeTaskLogs || !payload.log_name) return false;
      const entries = Array.isArray(activeTaskLogs.entries) ? activeTaskLogs.entries : [];
      activeTaskLogs.entries = entries;
      if (entries.some((entry) => entry.name === payload.log_name)) return true;
      entries.push({
        name: payload.log_name,
        path: '',
        rendered_content: null,
        debug_rendered_content: null,
        updated_at: new Date().toISOString(),
      });
      renderTaskLogButtons(entries);
      return true;
    }

    async function loadTaskLogs(taskId, { preserveSelection = true } = {}) {
      const requestToken = ++activeTaskLogRequestToken;
      const shouldScrollToBottomAfterLoad = !preserveSelection || !activeTaskLogs || !activeLogName;
      if (!preserveSelection) activeLogName = null;
      taskLogStatus.textContent = translateTask('loadingLogs');
      if (!preserveSelection || !activeTaskLogs || !taskLogViewer.textContent || taskLogViewer.textContent === translateTask('runtimeLogSummaryEmpty')) {
        taskLogViewer.textContent = translateTask('loadingLogs');
      }
      try {
        const response = await fetch(`/api/tasks/${taskId}/logs`);
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateTask('failedLoadLogs'));
        if (requestToken !== activeTaskLogRequestToken || taskId !== activeTaskId) return;
        renderTaskLogs(payload, { preserveSelection });
        if (shouldScrollToBottomAfterLoad) scrollTaskLogViewerToBottom();
      } catch (error) {
        if (requestToken !== activeTaskLogRequestToken || taskId !== activeTaskId) return;
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        taskLogName.textContent = translateTask('noLogSelected');
        taskLogStatus.textContent = translateTask('failedLoadLogs');
        taskLogViewer.textContent = translateTask('unableLoadLogs');
        taskLogViewerPinnedToBottom = true;
      }
    }

    async function loadTaskDetail(taskId, preserveTab = false, options = {}) {
      const { softRefresh = false, reloadArtifact = true } = options;
      const previousDetail = activeTaskDetail;
      const snapshot = options.snapshot || boardTaskSnapshots.get(taskId) || null;
      const nextTab = preserveTab ? activeTaskTab : taskChromeState(snapshot?.state).defaultTab;
      const shouldIncludeChangedFiles = nextTab === 'changed-files';
      const requestToken = ++activeTaskRequestToken;
      activeTaskId = taskId;
      taskModalError.hidden = true;
      setTaskDetailStale(false);
      if (!softRefresh) {
        activeTaskDetail = null;
        activeTaskLogs = null;
        activeChangedFileId = null;
        activeChangedFileDetail = null;
        activeInlineCommentAnchor = null;
        hydrateTaskModalChrome(snapshot, { preserveTab });
        if (!snapshot) {
          setTaskText('task-modal-title', 'modalTitle');
          document.getElementById('task-modal-subtitle').innerHTML = renderTaskSubtitleTags();
        }
        taskOverview.innerHTML = `<div class="muted">${escapeHtml(translateTask('loadingTaskDetails'))}</div>`;
        taskLogFiles.innerHTML = '';
        taskLogName.textContent = translateTask('noLogSelected');
        taskLogStatus.textContent = translateTask('selectRuntimeLog');
        taskLogViewer.textContent = translateTask('runtimeLogSummaryEmpty');
        taskLogViewerPinnedToBottom = true;
        taskChangedFiles.innerHTML = '';
        taskQaChecklistItems.innerHTML = '';
        taskQaChecklistBadges.innerHTML = '';
        renderDiffPlaceholder(translateTask('selectChangedTabToInspect'));
        setHumanReviewEditorContent('');
        reviewerQaTranscriptPinnedToBottom = true;
        reviewerQaPendingQuestion = '';
        reviewerQaPendingAnswer = '';
        reviewerQaQuestionInFlight = false;
        reviewerQaDraftBackup = '';
        reviewerQaLastRenderedSignature = '';
        setReviewerQaTranscript('', { preserveScroll: false });
        taskReviewerQaInput.value = '';
        taskMarkdownFiles.innerHTML = '';
        taskArtifactName.textContent = translateTask('noDocumentSelected');
        setPlanEditorContent('');
        setArtifactMode(false);
        activeArtifactName = null;
        activeLogName = null;
        planEditMode = false;
        savePlanButton.disabled = true;
        taskModeBadge.textContent = translateTask('viewerMode');
        setTaskEditorMessage(translateTask('selectMarkdownArtifact'));
        setTaskModalOpen(true);
      }
      try {
        const detailUrl = shouldIncludeChangedFiles
          ? `/api/tasks/${taskId}?include_changed_files=true`
          : `/api/tasks/${taskId}`;
        const response = await fetch(detailUrl);
        const detail = await response.json();
        if (!response.ok) throw new Error(detail.detail || translateTask('failedLoadTaskDetails'));
        if (requestToken !== activeTaskRequestToken || activeTaskId !== taskId) return;
        if (!shouldIncludeChangedFiles && detail.changed_files_available && !detail.changed_files.length && previousDetail?.changed_files?.length) {
          detail.changed_files = previousDetail.changed_files;
        }
        renderTaskOverview(detail);
        const preferredInitialTab = !preserveTab && detail.metadata.state === 'waiting-check-plans' && detail.markdown_files.length ? 'editor' : nextTab;
        const resolvedTab = !preserveTab && detail.metadata.state === 'human-verifying' && detail.changed_files_available ? 'changed-files' : preferredInitialTab;
        if (resolvedTab !== activeTaskTab) setTaskTab(resolvedTab, { load: false });
        if (resolvedTab === 'logs' && (!softRefresh || !activeTaskLogs)) await loadTaskLogs(taskId);
        if (resolvedTab === 'changed-files' && detail.changed_files_available) {
          await ensureChangedFileSummaries(taskId);
          if (requestToken !== activeTaskRequestToken || activeTaskId !== taskId || activeTaskTab !== 'changed-files' || !activeTaskDetail?.changed_files.length) return;
          await loadChangedFile(taskId, activeChangedFileId, true);
        }
        const shouldReloadArtifact = resolvedTab === 'editor'
          && detail.markdown_files.length
          && (
            reloadArtifact
            || !activeArtifactName
            || !detail.markdown_files.includes(activeArtifactName)
          );
        if (shouldReloadArtifact) await loadMarkdownArtifact(taskId, activeArtifactName);
      } catch (error) {
        if (requestToken !== activeTaskRequestToken || activeTaskId !== taskId) return;
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        if (!softRefresh) taskOverview.innerHTML = `<div class="muted">${escapeHtml(translateTask('unableLoadTaskDetails'))}</div>`;
        updateTaskDeleteState();
      }
    }

    function scheduleActiveTaskRefresh(options = {}) {
      const { reloadArtifact = true } = options;
      if (taskModal.hidden || !activeTaskId) return;
      if (activeTaskTab === 'editor' && planEditMode && activeArtifactName === 'PLAN.md' && isPlanDirty()) {
        setTaskDetailStale(true, translateTask('stalePlanMessage'));
        return;
      }
      clearTaskRefreshTimer();
      activeTaskRefreshTimer = window.setTimeout(() => {
        activeTaskRefreshTimer = null;
        loadTaskDetail(activeTaskId, true, { softRefresh: true, reloadArtifact }).catch((error) => {
          taskModalError.hidden = false;
          taskModalError.textContent = error.message;
        });
      }, 120);
    }

    async function deleteTask() {
      if (!activeTaskId || !activeTaskDetail) return;
      const confirmed = window.confirm(translateTask('deleteConfirm', { title: activeTaskDetail.metadata.title, taskId: activeTaskId }));
      if (!confirmed) return;
      deleteTaskButton.disabled = true;
      try {
        const response = await fetch(`/api/tasks/${activeTaskId}`, { method: 'DELETE' });
        let payload = null;
        try {
          payload = await response.json();
        } catch (_error) {
          payload = null;
        }
        if (!response.ok) throw new Error(payload && payload.detail ? payload.detail : 'Failed to delete task.');
        await loadBoard();
        setTaskModalOpen(false);
      } catch (error) {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        updateTaskDeleteState();
      }
    }

    async function loadChangedFile(taskId, changedFileId = null, silent = false) {
      if (!activeTaskDetail) return;
      if (!activeTaskDetail.changed_files.length) {
        await ensureChangedFileSummaries(taskId);
      }
      if (!activeTaskDetail.changed_files.length) return;
      const selected = changedFileId && activeTaskDetail.changed_files.some((file) => file.id === changedFileId)
        ? changedFileId
        : activeTaskDetail.changed_files[0].id;
      activeChangedFileId = selected;
      activeInlineCommentAnchor = null;
      renderChangedFileButtons(activeTaskDetail.changed_files);
      if (!silent) renderDiffPlaceholder('Loading stored patch...');
      try {
        const response = await fetch(`/api/tasks/${taskId}/changed-files/${encodeURIComponent(activeChangedFileId)}`);
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Failed to load changed file diff.');
        if (taskId !== activeTaskId || activeChangedFileId !== selected) return;
        renderChangedFileDetail(payload);
      } catch (error) {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        renderDiffPlaceholder('Failed to load changed file diff.');
      }
    }

    async function refreshActiveTaskDetailAfterComment(taskId) {
      if (!activeTaskDetail || !taskId) return;
      const response = await fetch(`/api/tasks/${taskId}?include_changed_files=true`);
      const detail = await response.json();
      if (!response.ok) throw new Error(detail.detail || translateTask('failedLoadTaskDetails'));
      if (taskId !== activeTaskId) return;
      activeTaskDetail = {
        ...activeTaskDetail,
        ...detail,
        changed_files_available: detail.changed_files_available,
        changed_files: detail.changed_files,
      };
      renderChangedFileButtons(activeTaskDetail.changed_files || []);
    }

    function applyChangedFileSummaryUpdate(summary) {
      if (!summary || !activeTaskDetail?.changed_files) return;
      activeTaskDetail.changed_files = activeTaskDetail.changed_files.map((file) => file.id === summary.id ? { ...file, ...summary } : file);
      if (activeChangedFileDetail?.summary?.id === summary.id) {
        activeChangedFileDetail = { ...activeChangedFileDetail, summary: { ...activeChangedFileDetail.summary, ...summary } };
        renderChangedFileSummaryCard(activeChangedFileDetail.summary);
      }
      renderChangedFileButtons(activeTaskDetail.changed_files || []);
    }

    async function setChangedFileViewed(taskId, changedFileId, viewed) {
      const response = await fetch(`/api/tasks/${taskId}/changed-files/${encodeURIComponent(changedFileId)}/viewed`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ viewed }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || translateHumanReview('viewedSaveError'));
      applyChangedFileSummaryUpdate(payload);
      return payload;
    }

    function nextUnviewedChangedFileId(currentFileId) {
      const files = activeTaskDetail?.changed_files || [];
      const currentIndex = files.findIndex((file) => file.id === currentFileId);
      if (currentIndex === -1) return null;
      const nextFile = files.slice(currentIndex + 1).find((file) => !file.viewed);
      return nextFile?.id || null;
    }

    async function submitInlineComment() {
      if (!activeTaskId || !activeChangedFileId || !activeInlineCommentAnchor) return;
      const restoreAnchor = activeInlineCommentAnchor;
      const body = currentInlineCommentDraft().trim();
      if (!body) {
        updateInlineCommentComposerState(translateHumanReview('commentComposerError'), 'error');
        return;
      }
      updateInlineCommentComposerState(translateHumanReview('commentComposerSubmitting'));
      try {
        const response = await fetch(`/api/tasks/${activeTaskId}/changed-files/${encodeURIComponent(activeChangedFileId)}/comments`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            path: activeInlineCommentAnchor.path,
            side: activeInlineCommentAnchor.side,
            line_number: activeInlineCommentAnchor.line_number,
            line_kind: activeInlineCommentAnchor.line_kind,
            hunk_header: activeInlineCommentAnchor.hunk_header,
            body,
          }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateHumanReview('commentComposerError'));
        activeInlineCommentDraft = '';
        activeInlineCommentAnchor = null;
        activeChangedFileId = payload.summary?.id || activeChangedFileId;
        await refreshActiveTaskDetailAfterComment(activeTaskId);
        renderChangedFileDetail(payload, { preserveScroll: true, scrollAnchor: restoreAnchor });
        taskModalError.hidden = true;
      } catch (error) {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        updateInlineCommentComposerState(error.message, 'error');
      }
    }

    async function deleteInlineComment(commentId) {
      if (!canUseLineComments() || taskDetailStale || !activeTaskId || !activeChangedFileDetail || !commentId) return;
      const changedFileId = activeChangedFileDetail.summary?.id || activeChangedFileId;
      syncInlineCommentDraftFromDom();
      const restoreAnchor = (activeChangedFileDetail.comments || []).find((comment) => String(comment.id) === String(commentId))?.anchor || activeInlineCommentAnchor;
      if (!changedFileId) return;
      deletingCommentId = String(commentId);
      renderChangedFileDetail(activeChangedFileDetail, { preserveScroll: true, keepComposer: true, scrollAnchor: restoreAnchor });
      try {
        const response = await fetch(`/api/tasks/${activeTaskId}/changed-files/${encodeURIComponent(changedFileId)}/comments/${encodeURIComponent(String(commentId))}`, {
          method: 'DELETE',
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateHumanReview('commentDeleteError'));
        activeChangedFileId = payload.summary?.id || changedFileId;
        await refreshActiveTaskDetailAfterComment(activeTaskId);
        renderChangedFileDetail(payload, { preserveScroll: true, keepComposer: true, scrollAnchor: restoreAnchor });
        taskModalError.hidden = true;
      } catch (error) {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
      } finally {
        deletingCommentId = null;
        if (activeChangedFileDetail) renderChangedFileDetail(activeChangedFileDetail, { preserveScroll: true, keepComposer: true, scrollAnchor: restoreAnchor });
      }
    }

    function captureArtifactViewerScrollState() {
      const state = {
        hostScrollTop: taskViewerHost.scrollTop,
        childScrolls: [],
      };
      const nodes = taskViewerHost.querySelectorAll('.toastui-editor-contents, .toastui-editor-main, .toastui-editor-md-container, .toastui-editor-ww-container, .log-viewer');
      nodes.forEach((node, index) => {
        state.childScrolls.push({
          index: index,
          className: node.className,
          scrollTop: node.scrollTop,
        });
      });
      return state;
    }

    function restoreArtifactViewerScrollState(state) {
      if (!state) return;
      taskViewerHost.scrollTop = state.hostScrollTop;
      const nodes = taskViewerHost.querySelectorAll('.toastui-editor-contents, .toastui-editor-main, .toastui-editor-md-container, .toastui-editor-ww-container, .log-viewer');
      state.childScrolls.forEach((item) => {
        if (nodes[item.index] && nodes[item.index].className === item.className) {
          nodes[item.index].scrollTop = item.scrollTop;
        } else {
          const matches = Array.from(nodes).filter((n) => n.className === item.className);
          if (matches.length === 1) {
            matches[0].scrollTop = item.scrollTop;
          }
        }
      });
    }

    async function loadMarkdownArtifact(taskId, filename = null) {
      if (!activeTaskDetail || !activeTaskDetail.markdown_files.length) return;
      const resolvedArtifactName = filename && activeTaskDetail.markdown_files.includes(filename) ? filename : preferredArtifact(activeTaskDetail.markdown_files);
      const isSameArtifact = (activeArtifactName === resolvedArtifactName);
      const scrollState = isSameArtifact ? captureArtifactViewerScrollState() : null;

      const requestToken = ++activeArtifactRequestToken;
      activeArtifactName = resolvedArtifactName;
      renderArtifactButtons(activeTaskDetail.markdown_files);
      const family = activeArtifactFamily(buildArtifactEntries(activeTaskDetail.markdown_files));
      taskArtifactName.textContent = family ? `${family.label} / ${activeArtifactName || translateTask('noDocumentSelected')}` : (activeArtifactName || translateTask('noDocumentSelected'));
      taskPlanEditorTitle.textContent = activeArtifactName || 'PLAN.md';
      setTaskEditorMessage(activeArtifactName ? translateTask('loadingArtifact', { name: activeArtifactName }) : translateTask('noMarkdownArtifactSelected'));
      setArtifactMode(false);
      updatePlanActionState();
      try {
        const response = await fetch(`/api/tasks/${taskId}/artifacts/${encodeURIComponent(activeArtifactName)}`);
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateTask('unableLoadArtifact', { name: activeArtifactName || 'artifact' }));
        if (requestToken !== activeArtifactRequestToken || taskId !== activeTaskId || activeArtifactName !== resolvedArtifactName) return;
        planSourceMarkdown = payload.content;
        setPlanEditorContent(payload.content);
        const editable = activeTaskDetail.metadata.state === 'waiting-check-plans' && activeArtifactName === 'PLAN.md' && planEditMode;
        setArtifactMode(editable);
        updatePlanActionState();
        setTaskEditorMessage('');
        if (isSameArtifact && scrollState) {
          requestAnimationFrame(() => restoreArtifactViewerScrollState(scrollState));
        } else {
          requestAnimationFrame(resetArtifactViewerScroll);
        }
      } catch (error) {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        setTaskEditorMessage(translateTask('unableLoadArtifact', { name: activeArtifactName || 'artifact' }));
      }
    }

    async function togglePlanEditMode() {
      if (!activeTaskDetail || activeTaskDetail.metadata.state !== 'waiting-check-plans' || activeArtifactName !== 'PLAN.md') return;
      planEditMode = !planEditMode;
      setArtifactMode(planEditMode);
      updatePlanActionState();
      await loadMarkdownArtifact(activeTaskId, activeArtifactName);
      if (!planEditMode && taskDetailStale) scheduleActiveTaskRefresh();
    }

    async function savePlanArtifact() {
      if (!activeTaskId) return;
      savePlanButton.disabled = true;
      setTaskEditorMessage(translateTask('savingPlan'));
      try {
        const normalizedContent = await replaceEmbeddedPlanImagesWithUploads(getPlanEditorContent());
        setPlanEditorContent(normalizedContent);
        const response = await fetch(`/api/tasks/${activeTaskId}/artifacts/PLAN.md`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: normalizedContent }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateTask('failedSavePlan'));
        planSourceMarkdown = normalizedContent;
        setTaskEditorMessage(translateTask('savedPlan'));
        if (taskDetailStale) scheduleActiveTaskRefresh();
      } catch (error) {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        setTaskEditorMessage(translateTask('saveFailed'));
      } finally {
        updatePlanActionState();
      }
    }

    async function approvePlan() {
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'waiting-check-plans') return;
      savePlanButton.disabled = true;
      approvePlanButton.disabled = true;
      setTaskEditorMessage(isPlanDirty() ? translateTask('savingBeforeApproval') : translateTask('approvingPlan'));
      try {
        if (isPlanDirty()) await savePlanArtifact();
        const response = await fetch(`/api/tasks/${activeTaskId}/approve-plan`, { method: 'POST' });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateTask('failedApprovePlan'));
        setTaskEditorMessage(translateTask('planApproved'));
        activeBoardPhase = 'implementation';
        boardPhaseManuallySelected = true;
        await loadBoard();
        setTaskModalOpen(false);
      } catch (error) {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        setTaskEditorMessage(translateTask('failedApprovePlan'));
      } finally {
        updatePlanActionState();
      }
    }

    function taskHasSplitProposal(metadata) {
      return Boolean(metadata?.split_proposal?.recommended && metadata?.split_proposal?.json_path);
    }

    async function splitPlan() {
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'waiting-check-plans') return;
      if (!taskHasSplitProposal(activeTaskDetail.metadata)) return;
      savePlanButton.disabled = true;
      approvePlanButton.disabled = true;
      splitPlanButton.disabled = true;
      setTaskEditorMessage(translateTask('splittingPlan'));
      try {
        if (isPlanDirty()) await savePlanArtifact();
        const response = await fetch(`/api/tasks/${activeTaskId}/split-plan`, { method: 'POST' });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateTask('failedSplitPlan'));
        setTaskEditorMessage(translateTask('planSplit'));
        activeBoardPhase = 'plan';
        boardPhaseManuallySelected = true;
        await loadBoard();
        setTaskModalOpen(false);
      } catch (error) {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        setTaskEditorMessage(translateTask('failedSplitPlan'));
      } finally {
        updatePlanActionState();
      }
    }

    async function startVerification() {
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'completed-reviews' || activeTaskDetail?.metadata?.lease?.run_id === 'manual-human-verifying') return;
      startVerificationButton.disabled = true;
      try {
        const response = await fetch(`/api/tasks/${activeTaskId}/start-verification`, { method: 'POST' });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateTask('failedStartVerification'));
        await loadBoard();
        await loadTaskDetail(activeTaskId, true);
        if (!taskTabChangedFiles.hidden) setTaskTab('changed-files');
      } catch (error) {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
      } finally {
        updateHumanVerificationState();
      }
    }

    async function retryVerificationApply() {
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'human-verifying') return;
      retryVerificationApplyButton.disabled = true;
      taskHumanReviewNoteStatus.textContent = translateTask('retryVerificationApply');
      try {
        const response = await fetch(`/api/tasks/${activeTaskId}/retry-verification-apply`, { method: 'POST' });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateTask('failedRetryVerificationApply'));
        await loadBoard();
        await loadTaskDetail(activeTaskId, true);
        if (!taskTabChangedFiles.hidden) setTaskTab('changed-files');
      } catch (error) {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        taskHumanReviewNoteStatus.textContent = error.message;
      } finally {
        updateHumanVerificationState();
      }
    }
