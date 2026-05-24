    function ensureMarkdownViewer(value) {
      const normalizedValue = activeArtifactName === 'PLAN.md' ? stripOuterMarkdownFence(value || '') : (value || '');
      const renderedValue = activeTaskId ? rewriteAttachmentPaths(normalizedValue, activeTaskId) : normalizedValue;
      if (window.toastui && window.toastui.Editor && window.toastui.Editor.factory) {
        taskViewerHost.innerHTML = '';
        markdownViewer = window.toastui.Editor.factory({
          el: taskViewerHost,
          viewer: true,
          initialValue: renderedValue,
        });
        return markdownViewer;
      }
      taskViewerHost.innerHTML = `<pre class="log-viewer">${escapeHtml(renderedValue)}</pre>`;
      return null;
    }

    function stripOuterMarkdownFence(value) {
      const trimmed = (value || '').trim();
      if (!trimmed.startsWith('```')) return value || '';
      const lines = trimmed.split(/\r?\n/);
      if (lines.length < 3) return value || '';
      const opening = (lines[0] || '').trim().toLowerCase();
      if (!['```', '```markdown', '```md'].includes(opening)) return value || '';
      if ((lines[lines.length - 1] || '').trim() !== '```') return value || '';
      return lines.slice(1, -1).join('\n').trim();
    }

    function resetArtifactViewerScroll() {
      taskViewerHost.scrollTop = 0;
      taskViewerHost.querySelectorAll('.toastui-editor-contents, .toastui-editor-main, .toastui-editor-md-container, .toastui-editor-ww-container, .log-viewer').forEach((node) => {
        node.scrollTop = 0;
      });
    }

    function rewriteAttachmentPaths(markdown, taskId) {
      return markdown.replace(/(!\[[^\]]*\]\()(_attachments\/[^)]+)(\))/g, `$1/api/tasks/${taskId}/attachments/$2$3`)
        .replace(/\/api\/tasks\/([^/]+)\/attachments\/_attachments\//g, '/api/tasks/$1/attachments/');
    }

    const embeddedPlanImageRe = /!\[(?<alt>[^\]]*)\]\((?<url>data:image\/(?<subtype>png|jpeg|jpg|gif|webp);base64,[^)]+)\)/g;
    const planAttachmentMaxDimension = 1280;
    const planAttachmentWebpQuality = 0.6;

    function attachmentUploadName(uploadName = '', fallbackType = 'image/png') {
      const trimmed = (uploadName || '').trim();
      if (trimmed) return trimmed;
      const fallbackExtension = fallbackType === 'image/jpeg' ? '.jpg' : fallbackType === 'image/gif' ? '.gif' : fallbackType === 'image/webp' ? '.webp' : '.png';
      return `image${fallbackExtension}`;
    }

    function attachmentRenamedToWebp(uploadName = '') {
      const normalized = attachmentUploadName(uploadName, 'image/png');
      return normalized.replace(/\.[a-zA-Z0-9]+$/, '') + '.webp';
    }

    function loadImageElement(src) {
      return new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error(translateTask('failedImageUpload')));
        image.src = src;
      });
    }

    function canvasToBlob(canvas, type, quality) {
      return new Promise((resolve) => {
        canvas.toBlob(resolve, type, quality);
      });
    }

    async function compressPlanAttachmentBlob(blob, uploadName = '') {
      const normalizedName = attachmentUploadName(uploadName, blob.type || 'image/png');
      if (!blob.type.startsWith('image/') || blob.type === 'image/gif') {
        return { blob, uploadName: normalizedName };
      }
      const objectUrl = URL.createObjectURL(blob);
      try {
        const image = await loadImageElement(objectUrl);
        const width = image.naturalWidth || image.width;
        const height = image.naturalHeight || image.height;
        if (!width || !height) return { blob, uploadName: normalizedName };
        const scale = Math.min(1, planAttachmentMaxDimension / Math.max(width, height));
        const canvas = document.createElement('canvas');
        canvas.width = Math.max(1, Math.round(width * scale));
        canvas.height = Math.max(1, Math.round(height * scale));
        const context = canvas.getContext('2d', { alpha: true });
        if (!context) return { blob, uploadName: normalizedName };
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        const compressedBlob = await canvasToBlob(canvas, 'image/webp', planAttachmentWebpQuality);
        if (!compressedBlob || !compressedBlob.size || compressedBlob.size >= blob.size) {
          return { blob, uploadName: normalizedName };
        }
        return { blob: compressedBlob, uploadName: attachmentRenamedToWebp(normalizedName) };
      } catch (_error) {
        return { blob, uploadName: normalizedName };
      } finally {
        URL.revokeObjectURL(objectUrl);
      }
    }

    async function dataUrlToBlob(dataUrl) {
      const response = await fetch(dataUrl);
      if (!response.ok) throw new Error(translateTask('failedImageUpload'));
      return response.blob();
    }

    async function replaceEmbeddedPlanImagesWithUploads(content) {
      const matches = Array.from(content.matchAll(embeddedPlanImageRe));
      if (!matches.length) return content;
      let normalized = '';
      let lastIndex = 0;
      for (const match of matches) {
        const alt = match.groups?.alt || '';
        const subtype = match.groups?.subtype || 'png';
        const dataUrl = match.groups?.url || '';
        const extension = subtype === 'jpeg' ? 'jpg' : subtype;
        const uploadName = `${(alt.trim() || 'image').replace(/\s+/g, '-').replace(/[^a-zA-Z0-9-_]/g, '').toLowerCase() || 'image'}.${extension}`;
        const blob = await dataUrlToBlob(dataUrl);
        const uploaded = await uploadPlanAttachment(blob, { uploadName });
        normalized += content.slice(lastIndex, match.index) + `![${alt}](${uploaded.relative_path})`;
        lastIndex = (match.index || 0) + match[0].length;
      }
      normalized += content.slice(lastIndex);
      return normalized;
    }

    function ensurePlanEditor() {
      if (planEditor || !window.toastui || !window.toastui.Editor) return planEditor;
      planEditor = new window.toastui.Editor({
        el: taskEditorHost,
        height: `${calculatePlanEditorHeight()}px`,
        initialEditType: 'markdown',
        previewStyle: 'tab',
        hideModeSwitch: true,
        toolbarItems: [
          ['heading', 'bold', 'italic'],
          ['ul', 'ol', 'task'],
          ['link', 'quote', 'code', 'image'],
        ],
        hooks: {
          addImageBlobHook: async (blob, callback) => {
            try {
              const uploaded = await uploadPlanAttachment(blob);
              callback(uploaded.relative_path, uploaded.filename);
              setTaskEditorMessage(translateTask('attachedPlanImage', { filename: uploaded.filename }));
              return false;
            } catch (error) {
              taskModalError.hidden = false;
              taskModalError.textContent = error.message;
              setTaskEditorMessage(translateTask('imageUploadFailed'));
              return false;
            }
          },
        },
        usageStatistics: false,
      });
      ensurePlanEditorResizeObserver();
      schedulePlanEditorHeightSync();
      return planEditor;
    }

    function planEditorHeightSyncNeeded() {
      return activeTaskTab === 'editor' && planEditMode && activeArtifactName === 'PLAN.md' && !taskPanelEditor.hidden && !taskPlanEditorShell.hidden;
    }

    function runPlanEditorHeightSyncAfterFrames(token, framesRemaining) {
      window.requestAnimationFrame(() => {
        if (token !== planEditorHeightSyncToken) return;
        if (framesRemaining > 1) {
          runPlanEditorHeightSyncAfterFrames(token, framesRemaining - 1);
          return;
        }
        if (!planEditorHeightSyncNeeded()) return;
        syncPlanEditorHeight();
      });
    }

    function schedulePlanEditorHeightSync(frameCount = 3) {
      const nextFrameCount = Math.max(1, Number(frameCount) || 1);
      const token = ++planEditorHeightSyncToken;
      runPlanEditorHeightSyncAfterFrames(token, nextFrameCount);
    }

    function ensurePlanEditorResizeObserver() {
      if (planEditorResizeObserver || typeof ResizeObserver !== 'function') return;
      planEditorResizeObserver = new ResizeObserver(() => {
        if (!planEditorHeightSyncNeeded()) return;
        schedulePlanEditorHeightSync(2);
      });
      [taskPanelEditor, taskPlanEditorShell, taskPlanEditorBody].forEach((node) => {
        if (node) planEditorResizeObserver.observe(node);
      });
    }

    function calculatePlanEditorHeight() {
      if (!taskPlanEditorBody) return 320;
      const shellHeight = Math.floor(taskPlanEditorBody.getBoundingClientRect().height || 0);
      return Math.max(220, shellHeight || 320);
    }

    function syncPlanEditorHeight() {
      const nextHeight = calculatePlanEditorHeight();
      taskEditorHost.style.height = `${nextHeight}px`;
      taskEditor.style.height = `${nextHeight}px`;
      if (planEditor && typeof planEditor.setHeight === 'function') {
        planEditor.setHeight(`${nextHeight}px`);
      }
    }

    function clearRequestFieldError(fieldName) {
      const fieldError = document.querySelector(`[data-error-for="${fieldName}"]`);
      if (fieldError) fieldError.textContent = '';
    }

    function syncRequestGoalField(value) {
      goalInput.value = typeof value === 'string' ? value : getRequestGoalEditorContent();
      return goalInput.value;
    }

    function currentRequestGoalValue() {
      return getRequestGoalEditorContent();
    }

    function requestDraftFieldLabel(fieldName) {
      const labels = {
        title: translateRequest('titleLabel'),
        goal: translateRequest('goalLabel'),
        background: translateRequest('backgroundLabel'),
        scope: translateRequest('scopeLabel'),
        out_of_scope: translateRequest('outOfScopeLabel'),
        constraints: translateRequest('constraintsLabel'),
        references: translateRequest('referencesLabel'),
        acceptance_criteria: translateRequest('acceptanceLabel'),
        target_repo: translateRequest('targetRepoLabel'),
        base_branch: translateRequest('baseBranchLabel'),
      };
      return labels[fieldName] || fieldName;
    }

    function captureRequestDraftScrollState() {
      const maxScrollTop = Math.max(0, requestDraftTranscript.scrollHeight - requestDraftTranscript.clientHeight);
      return {
        pinnedToBottom: requestDraftTranscriptPinnedToBottom,
        offsetFromBottom: maxScrollTop - requestDraftTranscript.scrollTop,
      };
    }

    function restoreRequestDraftScrollState(state) {
      if (!state || state.pinnedToBottom) {
        scrollRequestDraftTranscriptToBottom();
        return;
      }
      const maxScrollTop = Math.max(0, requestDraftTranscript.scrollHeight - requestDraftTranscript.clientHeight);
      requestDraftTranscript.scrollTop = Math.max(0, maxScrollTop - state.offsetFromBottom);
      updateRequestDraftTranscriptPinnedToBottom();
    }

    function scrollRequestDraftTranscriptToBottom() {
      requestDraftTranscript.scrollTop = requestDraftTranscript.scrollHeight;
      requestDraftTranscriptPinnedToBottom = true;
    }

    function updateRequestDraftTranscriptPinnedToBottom() {
      const remaining = requestDraftTranscript.scrollHeight - requestDraftTranscript.clientHeight - requestDraftTranscript.scrollTop;
      requestDraftTranscriptPinnedToBottom = remaining <= 24;
    }

    function renderRequestDraftTranscript() {
      const entries = requestDraftEntries.map((entry, index) => {
        const suggestions = entry.role === 'assistant' && entry.field_updates && Object.keys(entry.field_updates).length
          ? `<div class="request-draft-suggestions"><div class="request-draft-suggestions-title">${escapeHtml(translateRequest('draftSuggestedUpdates'))}</div><div class="request-draft-suggestion-list">${Object.entries(entry.field_updates).filter(([, value]) => value != null).map(([fieldName, value]) => `<div class="request-draft-suggestion"><div class="request-draft-suggestion-meta"><strong>${escapeHtml(requestDraftFieldLabel(fieldName))}</strong><span class="diff-badge">${escapeHtml(translateRequest('draftAutoAppliedBadge'))}</span></div><div class="request-draft-suggestion-copy"><span>${escapeHtml(value === '' ? translateRequest('draftClearField') : value)}</span></div></div>`).join('')}</div></div>`
          : '';
        const side = entry.role === 'user' ? 'current' : 'other';
        const liveBadge = entry.pending ? `<span class="transcript-live-badge">${escapeHtml(formatTranscriptLiveBadge(translateRequest('draftLiveSuffix')))}</span>` : '';
        return `<article class="request-draft-entry" data-role="${escapeHtml(entry.role)}" data-side="${side}"${entry.pending ? ' data-pending="true"' : ''}><div class="request-draft-shell"><div class="request-draft-meta"><span class="request-draft-role">${escapeHtml(entry.role === 'user' ? translateRequest('draftUserLabel') : translateRequest('draftTitle'))}</span><div class="request-draft-meta-badges">${liveBadge}</div></div><div class="request-draft-bubble">${escapeHtml(entry.text)}</div>${suggestions}</div></article>`;
      });
      return entries.length ? entries.join('') : `<p class="request-draft-empty">${escapeHtml(translateRequest('draftEmpty'))}</p>`;
    }

    function setRequestDraftTranscript() {
      const nextMarkup = renderRequestDraftTranscript();
      const nextSignature = JSON.stringify(requestDraftEntries);
      if (requestDraftLastRenderedSignature === nextSignature && requestDraftTranscript.innerHTML === nextMarkup) return;
      const scrollState = captureRequestDraftScrollState();
      requestDraftLastRenderedSignature = nextSignature;
      requestDraftTranscript.innerHTML = nextMarkup;
      requestAnimationFrame(() => restoreRequestDraftScrollState(scrollState));
    }

    function updateRequestDraftPanel(statusMessage = '') {
      attachRequestDraftImageButton.disabled = requestDraftAttachmentInFlight;
      sendRequestDraftButton.disabled = requestDraftMessageInFlight || requestDraftAttachmentInFlight || !(requestDraftInput.value || '').trim();
      requestDraftStatus.textContent = statusMessage || (requestDraftMessageInFlight ? translateRequest('draftSending') : translateRequest('draftIdle'));
      requestDraftAttachmentStatus.dataset.tone = requestDraftAttachmentInFlight ? 'busy' : requestDraftAttachmentStatusTone;
      requestDraftAttachmentStatus.textContent = requestDraftAttachmentInFlight
        ? translateRequest('draftAttachmentUploading', requestDraftAttachmentStatusVars)
        : (requestDraftAttachmentStatusMessage || translateRequest(requestDraftAttachmentStatusKey || 'draftAttachmentHelp', requestDraftAttachmentStatusVars));
      setRequestDraftTranscript();
      persistRequestComposerDraftPointer();
    }

    function seedRequestDraftInput(force = false) {
      if (!force && (requestDraftInput.value || '').trim()) return;
      requestDraftInput.value = '';
    }

    function setRequestDraftAttachmentStatusState(key, tone = 'neutral', variables = {}) {
      requestDraftAttachmentStatusKey = key;
      requestDraftAttachmentStatusMessage = '';
      requestDraftAttachmentStatusTone = tone;
      requestDraftAttachmentStatusVars = variables;
      updateRequestDraftPanel(requestDraftStatus.textContent);
    }

    function setRequestDraftAttachmentStatusMessage(message, tone = 'error') {
      requestDraftAttachmentStatusKey = '';
      requestDraftAttachmentStatusMessage = message || translateRequest('draftAttachmentFailed');
      requestDraftAttachmentStatusTone = tone;
      requestDraftAttachmentStatusVars = {};
      updateRequestDraftPanel(requestDraftStatus.textContent);
    }

    function buildRequestDraftImageMarkdown(uploaded) {
      const filename = uploaded?.filename || 'image';
      const alt = filename.replace(/\.[^.]+$/, '') || 'image';
      return `![${alt}](${uploaded.url})`;
    }

    function insertTextAtTextareaCursor(textarea, text) {
      const currentValue = textarea.value || '';
      const start = typeof textarea.selectionStart === 'number' ? textarea.selectionStart : currentValue.length;
      const end = typeof textarea.selectionEnd === 'number' ? textarea.selectionEnd : currentValue.length;
      const prefix = currentValue.slice(0, start);
      const suffix = currentValue.slice(end);
      const needsLeadingBreak = prefix && !prefix.endsWith('\n');
      const needsTrailingBreak = suffix && !suffix.startsWith('\n');
      const insertion = `${needsLeadingBreak ? '\n' : ''}${text}${needsTrailingBreak ? '\n' : ''}`;
      const nextValue = `${prefix}${insertion}${suffix}`;
      const caret = prefix.length + insertion.length;
      textarea.value = nextValue;
      textarea.focus();
      textarea.setSelectionRange(caret, caret);
    }

    async function attachImagesToRequestDraft(files) {
      const imageFiles = Array.from(files || []).filter((file) => file && typeof file.type === 'string' && file.type.startsWith('image/'));
      if (!imageFiles.length || requestDraftAttachmentInFlight) return;
      const sessionToken = requestDraftSessionToken;
      requestDraftAttachmentInFlight = true;
      formError.hidden = true;
      try {
        for (const file of imageFiles) {
          setRequestDraftAttachmentStatusState('draftAttachmentUploading', 'busy', { filename: file.name || 'image' });
          const uploaded = await uploadRequestAttachment(file, { uploadName: file.name || '' });
          if (sessionToken !== requestDraftSessionToken) return;
          insertTextAtTextareaCursor(requestDraftInput, buildRequestDraftImageMarkdown(uploaded));
          setRequestDraftAttachmentStatusState('draftAttachmentAttached', 'success', { filename: uploaded.filename || file.name || 'image' });
          void syncRequestComposerDraftState({ silent: true });
        }
      } catch (error) {
        if (sessionToken !== requestDraftSessionToken) return;
        setRequestDraftAttachmentStatusMessage(error.message || translateRequest('draftAttachmentFailed'), 'error');
      } finally {
        if (sessionToken !== requestDraftSessionToken) return;
        requestDraftAttachmentInFlight = false;
        requestDraftImageInput.value = '';
        updateRequestDraftPanel(requestDraftStatus.textContent);
      }
    }

    function requestDraftClipboardImageFiles(event) {
      return Array.from(event.clipboardData?.items || [])
        .filter((item) => item && typeof item.type === 'string' && item.type.startsWith('image/'))
        .map((item) => item.getAsFile())
        .filter(Boolean);
    }

    function updateRequestDraftDropTarget(active) {
      requestDraftComposer.classList.toggle('is-drop-target', active);
    }

    function serializeRequestDraftTranscript() {
      return requestDraftEntries
        .filter((entry) => !entry.pending)
        .map((entry) => ({ role: entry.role, content: entry.text }));
    }

    function currentRequestDraftPayload(message) {
      syncRequestGoalField();
      const payload = Object.fromEntries(new FormData(requestForm).entries());
      payload.request_draft_id = requestDraftId || null;
      payload.plan_auto_approve = document.getElementById('plan_auto_approve').checked;
      payload.request_upload_token = requestUploadToken || '';
      payload.active_tab = activeRequestComposerTab;
      payload.request_draft_input = requestDraftInput.value || '';
      payload.transcript = serializeRequestDraftTranscript();
      payload.message = message;
      return payload;
    }

    function applyRequestDraftFieldUpdate(fieldName, value, options = {}) {
      const { sync = true, statusMessage = null } = options;
      if (value == null) return;
      value = preserveRequestDraftAttachments(fieldName, value);
      if (fieldName === 'goal') {
        setRequestGoalEditorContent(value);
      } else if (fieldName === 'background') {
        requestBackgroundInput.value = value;
      } else {
        const field = requestForm.elements.namedItem(fieldName);
        if (!field || !('value' in field)) return;
        field.value = value;
      }
      if (fieldName === 'target_repo') {
        targetRepoInput.dataset.autofilled = 'false';
        queueTargetRepoBranchLookup();
      }
      if (fieldName === 'base_branch') baseBranchInput.dataset.autofilled = 'false';
      if (fieldName === 'scope') scopeField.dataset.autofilled = 'false';
      if (fieldName === 'out_of_scope') outOfScopeField.dataset.autofilled = 'false';
      if (fieldName === 'acceptance_criteria') acceptanceCriteriaField.dataset.autofilled = 'false';
      clearRequestFieldError(fieldName);
      formError.hidden = true;
      if (statusMessage !== false) updateRequestDraftPanel(statusMessage || translateRequest('draftApplied', { field: requestDraftFieldLabel(fieldName) }));
      if (sync) void syncRequestComposerDraftState({ silent: true });
    }

    function applyRequestDraftFieldUpdates(fieldUpdates, options = {}) {
      const { sync = true, statusMessage = null } = options;
      const updates = Object.entries(fieldUpdates || {}).filter(([, value]) => value != null);
      if (!updates.length) return [];
      const labels = [];
      updates.forEach(([fieldName, value]) => {
        applyRequestDraftFieldUpdate(fieldName, value, { sync: false, statusMessage: false });
        labels.push(requestDraftFieldLabel(fieldName));
      });
      const uniqueLabels = [...new Set(labels)];
      if (statusMessage !== false) {
        const nextStatus = statusMessage || translateRequest('draftAutoApplied', { fields: uniqueLabels.join(', ') });
        updateRequestDraftPanel(nextStatus);
      }
      if (sync) void syncRequestComposerDraftState({ silent: true });
      return uniqueLabels;
    }

    function currentRequestDraftFieldValue(fieldName) {
      if (fieldName === 'goal') return currentRequestGoalValue();
      if (fieldName === 'background') return requestBackgroundInput.value || '';
      const field = requestForm.elements.namedItem(fieldName);
      if (!field || !('value' in field)) return '';
      return field.value || '';
    }

    function preserveRequestDraftAttachments(fieldName, nextValue) {
      if (!['goal', 'background'].includes(fieldName)) return nextValue;
      if (!nextValue.trim()) return nextValue;
      const currentValue = currentRequestDraftFieldValue(fieldName);
      const attachments = currentValue.match(requestDraftAttachmentRegex) || [];
      if (!attachments.length) return nextValue;
      let preservedValue = nextValue;
      attachments.forEach((attachment) => {
        if (preservedValue.includes(attachment)) return;
        preservedValue = preservedValue.trimEnd();
        preservedValue = preservedValue ? `${preservedValue}\n\n${attachment}` : attachment;
      });
      return preservedValue;
    }

    function notifyRequestDraftTargetRepoRequired() {
      const message = translateRequest('draftTargetRepoRequired');
      const targetRepoField = targetRepoInput.closest('.field');
      const targetRepoError = document.querySelector('[data-error-for="target_repo"]');
      if (targetRepoError) targetRepoError.textContent = message;
      formError.hidden = false;
      formError.textContent = message;
      updateRequestDraftPanel(message);
      if (targetRepoField) {
        targetRepoField.classList.remove('field-attention');
        void targetRepoField.offsetWidth;
        targetRepoField.classList.add('field-attention');
        targetRepoField.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
      requestAnimationFrame(() => {
        if (btnBrowseTargetRepo) {
          btnBrowseTargetRepo.focus();
          return;
        }
        targetRepoInput.focus();
      });
    }

    async function sendRequestDraftMessage() {
      const message = (requestDraftInput.value || '').trim();
      if (!message || requestDraftMessageInFlight) {
        updateRequestDraftPanel();
        return;
      }
      if (!normalizeRepoPath(targetRepoInput.value)) {
        notifyRequestDraftTargetRepoRequired();
        return;
      }
      await ensureRequestComposerDraft();
      const sessionToken = requestDraftSessionToken;
      formError.hidden = true;
      requestDraftPendingMessage = message;
      requestDraftMessageInFlight = true;
      requestDraftEntries = [
        ...requestDraftEntries,
        { role: 'user', text: message },
        { role: 'assistant', text: translateRequest('draftSending'), pending: true, field_updates: {} },
      ];
      requestDraftInput.value = '';
      requestDraftTranscriptPinnedToBottom = true;
      updateRequestDraftPanel(translateRequest('draftSending'));
      try {
        const response = await fetch('/api/request-drafts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(currentRequestDraftPayload(message)),
        });
        const rawResponseText = await response.text();
        let payload = {};
        if (rawResponseText) {
          try {
            payload = JSON.parse(rawResponseText);
          } catch (_error) {
            if (!response.ok) throw new Error(rawResponseText.trim() || translateRequest('draftReplyError'));
            throw new Error(translateRequest('draftReplyError'));
          }
        }
        if (!response.ok) throw new Error(payload.detail || rawResponseText.trim() || translateRequest('draftReplyError'));
        if (sessionToken !== requestDraftSessionToken) return;
        requestDraftId = payload.request_draft_id || requestDraftId;
        requestUploadToken = payload.request_upload_token || requestUploadToken;
        requestDraftEntries = Array.isArray(payload.transcript)
          ? payload.transcript.map((entry) => ({ role: entry.role, text: entry.content || '', field_updates: entry.field_updates || {} }))
          : [
              ...requestDraftEntries.slice(0, -1),
              { role: 'assistant', text: payload.reply || '', field_updates: payload.field_updates || {} },
            ];
        persistRequestComposerDraftPointer();
        applyRequestDraftFieldUpdates(payload.field_updates || {}, { sync: true });
        if (!payload.field_updates || !Object.keys(payload.field_updates).length) updateRequestDraftPanel();
      } catch (error) {
        if (sessionToken !== requestDraftSessionToken) return;
        requestDraftInput.value = requestDraftPendingMessage;
        requestDraftEntries = requestDraftEntries.slice(0, -1);
        formError.hidden = false;
        formError.textContent = error.message;
        updateRequestDraftPanel(error.message);
      } finally {
        if (sessionToken !== requestDraftSessionToken) return;
        requestDraftPendingMessage = '';
        requestDraftMessageInFlight = false;
        updateRequestDraftPanel(requestDraftStatus.textContent);
      }
    }

    function resetRequestDraftState() {
      requestDraftSessionToken += 1;
      requestDraftId = '';
      requestDraftEntries = [];
      requestDraftPendingMessage = '';
      requestDraftMessageInFlight = false;
      requestDraftAttachmentInFlight = false;
      requestDraftAttachmentStatusKey = '';
      requestDraftAttachmentStatusMessage = '';
      requestDraftAttachmentStatusTone = 'neutral';
      requestDraftAttachmentStatusVars = {};
      requestDraftDropDepth = 0;
      requestDraftImageInput.value = '';
      updateRequestDraftDropTarget(false);
      requestDraftLastRenderedSignature = '';
      requestDraftTranscriptPinnedToBottom = true;
      seedRequestDraftInput(true);
      updateRequestDraftPanel();
    }
