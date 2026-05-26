
    function generateRequestUploadToken() {
      if (window.crypto && typeof window.crypto.randomUUID === 'function') return window.crypto.randomUUID();
      return `request-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
    }

    async function cleanupRequestUploads(uploadToken = requestUploadToken) {
      if (!uploadToken) return;
      try {
        await fetch(`/api/request-uploads/${encodeURIComponent(uploadToken)}`, { method: 'DELETE' });
      } catch (_error) {
      }
    }

    async function uploadRequestAttachment(blob, options = {}) {
      const { uploadName = '' } = options;
      const uploadToken = requestUploadToken || generateRequestUploadToken();
      requestUploadToken = uploadToken;
      const optimized = await compressPlanAttachmentBlob(blob, uploadName || blob.name || '');
      const formData = new FormData();
      formData.append('file', optimized.blob, optimized.uploadName);
      const response = await fetch(`/api/request-uploads?upload_token=${encodeURIComponent(uploadToken)}`, {
        method: 'POST',
        body: formData,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || translateTask('failedImageUpload'));
      return payload;
    }

    function ensureRequestGoalEditor() {
      if (requestGoalEditor || !window.toastui || !window.toastui.Editor) return requestGoalEditor;
      requestGoalEditor = new window.toastui.Editor({
        el: requestGoalEditorHost,
        height: '320px',
        initialEditType: 'markdown',
        initialValue: goalInput.value || requestGoalEditorFallback.value || '',
        previewStyle: 'tab',
        hideModeSwitch: true,
        hooks: {
          addImageBlobHook: async (blob, callback) => {
            try {
              const uploaded = await uploadRequestAttachment(blob, { uploadName: blob.name || '' });
              callback(uploaded.url, uploaded.filename);
              syncRequestGoalField(requestGoalEditor.getMarkdown());
              clearRequestFieldError('goal');
              return false;
            } catch (error) {
              formError.hidden = false;
              formError.textContent = error.message;
              return false;
            }
          },
        },
        usageStatistics: false,
      });
      requestGoalEditor.on('change', () => {
        syncRequestGoalField(requestGoalEditor.getMarkdown());
        clearRequestFieldError('goal');
        void syncRequestComposerDraftState({ silent: true });
      });
      requestGoalEditorFallback.hidden = true;
      requestGoalEditorHost.hidden = false;
      return requestGoalEditor;
    }

    function setRequestGoalEditorContent(value, options = {}) {
      const { initialize = true } = options;
      const nextValue = value || '';
      syncRequestGoalField(nextValue);
      requestGoalEditorFallback.value = nextValue;
      const editor = initialize ? ensureRequestGoalEditor() : requestGoalEditor;
      if (editor) {
        editor.setMarkdown(nextValue);
        requestGoalEditorFallback.hidden = true;
        requestGoalEditorHost.hidden = false;
        return;
      }
      requestGoalEditorHost.hidden = true;
      requestGoalEditorFallback.hidden = false;
      requestGoalEditorFallback.value = nextValue;
    }

    function getRequestGoalEditorContent() {
      const editor = ensureRequestGoalEditor();
      if (editor) return editor.getMarkdown();
      return requestGoalEditorFallback.value;
    }

    async function uploadPlanAttachment(blob, options = {}) {
      const { uploadName = '' } = options;
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'waiting-check-plans' || activeArtifactName !== 'PLAN.md') {
        throw new Error(translateTask('imageAttachmentsRestricted'));
      }
      const optimized = await compressPlanAttachmentBlob(blob, uploadName || blob.name || '');
      const formData = new FormData();
      formData.append('file', optimized.blob, optimized.uploadName);
      setTaskEditorMessage(translateTask('uploadingImage'));
      const response = await fetch(`/api/tasks/${activeTaskId}/attachments?artifact=PLAN.md`, {
        method: 'POST',
        body: formData,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || translateTask('failedImageUpload'));
      return payload;
    }

    async function uploadHumanReviewAttachment(blob, options = {}) {
      const { uploadName = '' } = options;
      const notePath = activeTaskDetail?.human_review?.note_path || activeTaskDetail?.metadata?.human_verification?.note_path || '';
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'human-verifying' || !notePath) {
        throw new Error(translateTask('humanReviewImageAttachmentsRestricted'));
      }
      const optimized = await compressPlanAttachmentBlob(blob, uploadName || blob.name || '');
      const formData = new FormData();
      formData.append('file', optimized.blob, optimized.uploadName);
      taskHumanReviewNoteStatus.textContent = translateTask('uploadingImage');
      const response = await fetch(`/api/tasks/${activeTaskId}/attachments?artifact=${encodeURIComponent(notePath)}`, {
        method: 'POST',
        body: formData,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || translateTask('failedImageUpload'));
      return payload;
    }

    function setPlanEditorContent(value) {
      ensureMarkdownViewer(value || '');
      if (planEditor) planEditor.setMarkdown(value || '');
      taskEditor.value = value || '';
      if (planEditorHeightSyncNeeded()) schedulePlanEditorHeightSync();
    }

    function setTaskEditorMessage(value) {
      taskEditorStatus.textContent = '';
      taskPlanEditorShellStatus.textContent = value;
    }

    function getPlanEditorContent() {
      const editor = ensurePlanEditor();
      if (editor) return editor.getMarkdown();
      return taskEditor.value;
    }

    function ensureHumanReviewEditor() {
      if (humanReviewEditor || !window.toastui || !window.toastui.Editor) return humanReviewEditor;
      humanReviewEditor = new window.toastui.Editor({
        el: taskHumanReviewEditorHost,
        height: `${calculateHumanReviewEditorHeight()}px`,
        initialEditType: 'markdown',
        previewStyle: 'tab',
        hideModeSwitch: true,
        hooks: {
          addImageBlobHook: async (blob, callback) => {
            try {
              const uploaded = await uploadHumanReviewAttachment(blob, { uploadName: blob.name || '' });
              callback(uploaded.relative_path, uploaded.filename);
              taskHumanReviewNoteStatus.textContent = translateTask('attachedHumanReviewImage', { filename: uploaded.filename });
              return false;
            } catch (error) {
              taskHumanReviewNoteStatus.textContent = error.message || translateTask('imageUploadFailed');
              return false;
            }
          },
        },
        usageStatistics: false,
      });
      humanReviewEditor.on('change', updateHumanReviewPanel);
      syncHumanReviewEditorHeight();
      return humanReviewEditor;
    }

    function calculateHumanReviewEditorHeight() {
      if (!taskHumanReviewEditorShell) return 320;
      const shellHeight = Math.floor(taskHumanReviewEditorShell.getBoundingClientRect().height || 0);
      return Math.max(220, shellHeight || 320);
    }

    function syncHumanReviewEditorHeight() {
      const nextHeight = calculateHumanReviewEditorHeight();
      taskHumanReviewEditorHost.style.height = `${nextHeight}px`;
      taskHumanReviewEditorFallback.style.height = `${nextHeight}px`;
      if (humanReviewEditor && typeof humanReviewEditor.setHeight === 'function') {
        humanReviewEditor.setHeight(`${nextHeight}px`);
      }
    }

    function setHumanReviewEditorContent(value) {
      humanReviewSourceMarkdown = value || '';
      const editor = ensureHumanReviewEditor();
      if (editor) {
        editor.setMarkdown(humanReviewSourceMarkdown);
        requestAnimationFrame(() => syncHumanReviewEditorHeight());
        taskHumanReviewEditorFallback.hidden = true;
        updateHumanReviewPanel();
        return;
      }
      taskHumanReviewEditorFallback.hidden = false;
      taskHumanReviewEditorFallback.value = humanReviewSourceMarkdown;
      requestAnimationFrame(() => syncHumanReviewEditorHeight());
      updateHumanReviewPanel();
    }

    function getHumanReviewEditorContent() {
      const editor = ensureHumanReviewEditor();
      if (editor) return editor.getMarkdown();
      return taskHumanReviewEditorFallback.value;
    }

    function humanReviewNoteDirty() {
      return getHumanReviewEditorContent().replace(/\s+$/, '') !== humanReviewSourceMarkdown.replace(/\s+$/, '');
    }

    async function saveHumanReviewNoteIfNeeded() {
      if (!activeTaskId || activeTaskDetail?.metadata?.state !== 'human-verifying') return;
      if (!humanReviewNoteDirty()) return;
      taskHumanReviewNoteStatus.textContent = translateHumanReview('noteSaving');
      saveHumanReviewNoteButton.disabled = true;
      const response = await fetch(`/api/tasks/${activeTaskId}/human-review-note`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: getHumanReviewEditorContent() }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || translateTask('failedHumanReviewSave'));
      setHumanReviewEditorContent(payload.content || '');
      taskHumanReviewNoteStatus.textContent = translateHumanReview('noteSaved');
      updateHumanReviewPanel();
    }

    function updateHumanReviewPanel() {
      const canVerify = activeTaskDetail?.metadata?.state === 'human-verifying';
      const integrationApplied = Boolean(activeTaskDetail?.metadata?.integration?.applied);
      const currentCommentCount = Number(activeTaskDetail?.human_review?.total_comment_count || 0);
      const qaProgress = qaChecklistProgress();
      const incompleteQaCount = canVerify ? Math.max(0, qaProgress.required - qaProgress.completedRequired) : 0;
      const noteExists = canVerify && getHumanReviewEditorContent().trim().length > 0;
      const hasFeedback = !integrationApplied || noteExists || currentCommentCount > 0;
      const approvalBlockedByContent = !integrationApplied || noteExists || currentCommentCount > 0 || incompleteQaCount > 0;
      taskHumanReviewPanel.hidden = !canVerify;
      requestChangesShell.hidden = !canVerify;
      approveHumanReviewShell.hidden = !canVerify;
      if (!canVerify) {
        setApprovalGateNotice();
        return;
      }
      taskHumanReviewNoteStatus.textContent = humanReviewNoteDirty() ? translateHumanReview('noteSaving') : translateHumanReview('noteStatus');
      saveHumanReviewNoteButton.disabled = taskDetailStale || !humanReviewNoteDirty();
      retryVerificationApplyButton.disabled = taskDetailStale || integrationApplied;
      requestChangesButton.disabled = taskDetailStale || !hasFeedback;
      approveHumanReviewButton.disabled = taskDetailStale || approvalBlockedByContent;

      let requestChangesReason = '';
      if (taskDetailStale) {
        requestChangesReason = translateHumanReview('actionBlockedStale');
      } else if (!hasFeedback) {
        requestChangesReason = translateHumanReview('requestChangesNeedsFeedback');
      }

      let approvalReason = '';
      if (taskDetailStale) {
        approvalReason = translateHumanReview('actionBlockedStale');
      } else if (!integrationApplied) {
        approvalReason = translateTask('approvalBlockedRetryApply');
      } else if (noteExists && currentCommentCount > 0) {
        approvalReason = translateHumanReview(currentCommentCount === 1 ? 'approvalBlockedNoteAndCommentsOne' : 'approvalBlockedNoteAndCommentsMany', { count: currentCommentCount });
      } else if (noteExists) {
        approvalReason = translateHumanReview('approvalBlockedNoteOnly');
      } else if (currentCommentCount > 0) {
        approvalReason = translateHumanReview(currentCommentCount === 1 ? 'approvalBlockedCommentsOnlyOne' : 'approvalBlockedCommentsOnlyMany', { count: currentCommentCount });
      } else if (incompleteQaCount > 0) {
        approvalReason = translateHumanReview(incompleteQaCount === 1 ? 'approvalBlockedQaOne' : 'approvalBlockedQaMany', { count: incompleteQaCount });
      }

      requestChangesShell.title = requestChangesReason;
      requestChangesButton.title = requestChangesReason;
      approveHumanReviewShell.title = approvalReason;
      approveHumanReviewButton.title = approvalReason;

      taskHumanReviewApprovalStatus.hidden = !approvalReason;
      taskHumanReviewApprovalStatus.textContent = approvalReason || '';
      taskHumanReviewApprovalStatus.dataset.tone = approvalReason ? 'warning' : 'neutral';
      if (taskDetailStale) {
        setApprovalGateNotice();
      } else if (!integrationApplied) {
        setApprovalGateNotice({
          title: translateHumanReview('approvalGateRetryTitle'),
          body: translateHumanReview('approvalGateRetryBody'),
          detailsHtml: renderLocalQaGitHint(),
        });
      } else if (noteExists || currentCommentCount > 0) {
        setApprovalGateNotice({
          title: translateHumanReview('approvalGateReviewTitle'),
          body: translateHumanReview('approvalGateReviewBody'),
          detailsHtml: renderLocalQaGitHint(),
        });
      } else if (incompleteQaCount > 0) {
        setApprovalGateNotice({
          title: translateHumanReview('approvalGateQaTitle'),
          body: translateHumanReview(incompleteQaCount === 1 ? 'approvalGateQaBodyOne' : 'approvalGateQaBodyMany', { count: incompleteQaCount }),
          actionLabel: translateHumanReview('approvalGateQaAction'),
          action: 'qa-checklist',
          detailsHtml: renderLocalQaGitHint(),
        });
      } else {
        setApprovalGateNotice();
      }
    }

    function setReviewerQaTranscript(value, { preserveScroll = true } = {}) {
      reviewerQaSourceMarkdown = value || '';
      const entries = buildReviewerQaEntries();
      const nextMarkup = renderReviewerQaTranscript(entries);
      const nextSignature = JSON.stringify({ source: reviewerQaSourceMarkdown, pendingQuestion: reviewerQaPendingQuestion, pendingAnswer: reviewerQaPendingAnswer, inFlight: reviewerQaQuestionInFlight, rerequestInFlight: reviewerQaRerequestInFlight, state: activeTaskDetail?.metadata?.state || '', stale: taskDetailStale });
      if (reviewerQaLastRenderedSignature === nextSignature && taskReviewerQaTranscript.innerHTML === nextMarkup) return;
      const scrollState = preserveScroll ? captureReviewerQaScrollState() : { pinnedToBottom: true, offsetFromBottom: 0 };
      reviewerQaLastRenderedSignature = nextSignature;
      taskReviewerQaTranscript.innerHTML = nextMarkup;
      requestAnimationFrame(() => restoreReviewerQaScrollState(scrollState));
    }

    function updateReviewerQaPanel() {
      const state = activeTaskDetail?.metadata?.state;
      const canAskReviewer = state === 'completed-reviews' || state === 'human-verifying';
      taskReviewerQaPanel.hidden = !canAskReviewer;
      setReviewerQaTranscript(reviewerQaSourceMarkdown);
      if (!canAskReviewer) return;
      askReviewerQuestionButton.disabled = taskDetailStale || reviewerQaQuestionInFlight || reviewerQaRerequestInFlight || !(taskReviewerQaInput.value || '').trim();
      if (taskDetailStale) {
        taskReviewerQaStatus.textContent = 'Refresh task details before asking another reviewer question.';
        return;
      }
      if (reviewerQaRerequestInFlight) {
        taskReviewerQaStatus.textContent = '재요청을 보내는 중...';
        return;
      }
      if (reviewerQaQuestionInFlight) {
        taskReviewerQaStatus.textContent = reviewerQaPendingAnswer
          ? 'Reviewer is responding live. The transcript will finalize automatically when the run completes.'
          : 'Reviewer is responding…';
        return;
      }
      taskReviewerQaStatus.textContent = reviewerQaSourceMarkdown
        ? 'Saved in the current reviewer Q&A artifact. Check the Logs tab for reviewer-qa.jsonl when you want the Thinking output.'
        : 'Ask the reviewer follow-up questions. OpenCode reviewer runs use Thinking Mode when available, and the runtime log is saved as reviewer-qa.jsonl.';
    }

    async function rerequestReviewerQa() {
      if (!activeTaskId || taskDetailStale || reviewerQaRerequestInFlight || !canShowReviewerQaRerequestAction()) return;
      reviewerQaRerequestInFlight = true;
      taskModalError.hidden = true;
      taskModalError.textContent = '';
      updateReviewerQaPanel();
      try {
        const response = await fetch(`/api/tasks/${activeTaskId}/reviewer-qa-rerequest`, {
          method: 'POST',
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Failed to re-request reviewer follow-up.');
        await loadBoard();
        await loadTaskDetail(activeTaskId, true, { softRefresh: true, reloadArtifact: false });
      } catch (error) {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        taskReviewerQaStatus.textContent = error.message;
      } finally {
        reviewerQaRerequestInFlight = false;
        updateReviewerQaPanel();
      }
    }

    async function askReviewerQuestion() {
      if (!activeTaskId || taskDetailStale || reviewerQaRerequestInFlight) return;
      const question = (taskReviewerQaInput.value || '').trim();
      let reviewerQaRequestCommitted = false;
      if (!question) {
        updateReviewerQaPanel();
        return;
      }
      reviewerQaDraftBackup = question;
      reviewerQaPendingQuestion = question;
      reviewerQaPendingAnswer = '';
      reviewerQaQuestionInFlight = true;
      taskReviewerQaInput.value = '';
      reviewerQaTranscriptPinnedToBottom = true;
      setReviewerQaTranscript(reviewerQaSourceMarkdown, { preserveScroll: false });
      taskReviewerQaStatus.textContent = 'Asking reviewer…';
      askReviewerQuestionButton.disabled = true;
      updateReviewerQaLiveRefresh();
      try {
        const response = await fetch(`/api/tasks/${activeTaskId}/reviewer-qa`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Failed to ask reviewer question.');
        reviewerQaRequestCommitted = true;
        taskModalError.hidden = true;
        reviewerQaPendingAnswer = payload.answer || reviewerQaPendingAnswer;
        reviewerQaTranscriptPinnedToBottom = true;
        setReviewerQaTranscript(reviewerQaSourceMarkdown, { preserveScroll: false });
        reviewerQaPendingQuestion = '';
        reviewerQaPendingAnswer = '';
        reviewerQaQuestionInFlight = false;
        reviewerQaDraftBackup = '';
        await loadTaskDetail(activeTaskId, true, { softRefresh: true, reloadArtifact: false });
        await loadTaskLogs(activeTaskId, { preserveSelection: false });
        taskReviewerQaStatus.textContent = payload.log_name
          ? `Reviewer answered. Thinking output is available in ${payload.log_name}.`
          : 'Reviewer answered.';
      } catch (error) {
        if (!reviewerQaRequestCommitted) taskReviewerQaInput.value = reviewerQaDraftBackup;
        reviewerQaPendingQuestion = '';
        reviewerQaPendingAnswer = '';
        reviewerQaQuestionInFlight = false;
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        taskReviewerQaStatus.textContent = error.message;
      } finally {
        reviewerQaDraftBackup = '';
        if (!reviewerQaRequestCommitted) {
          setReviewerQaTranscript(activeTaskDetail?.human_review?.reviewer_qa_markdown || reviewerQaSourceMarkdown, { preserveScroll: true });
        }
        updateReviewerQaLiveRefresh();
        updateReviewerQaPanel();
      }
    }

    function setArtifactMode(editing) {
      taskModeBadge.textContent = editing ? translateTask('editMode') : translateTask('viewerMode');
      taskViewerHost.hidden = editing;
      taskPlanEditorShell.hidden = !editing;
      taskEditorHost.hidden = !editing;
      if (editing) {
        const editor = ensurePlanEditor();
        if (editor) {
          editor.setMarkdown(taskEditor.value || planSourceMarkdown || '');
          taskEditor.hidden = true;
          taskEditorHost.hidden = false;
          schedulePlanEditorHeightSync();
        } else {
          taskEditor.hidden = false;
          taskEditor.disabled = false;
          taskEditor.readOnly = false;
          schedulePlanEditorHeightSync();
        }
      } else {
        taskEditorHost.hidden = true;
        taskEditor.hidden = true;
        taskEditor.disabled = true;
        taskEditor.readOnly = true;
      }
    }

    function isPlanDirty() {
      return getPlanEditorContent().replace(/\s+$/, '') !== planSourceMarkdown.replace(/\s+$/, '');
    }

    function updatePlanActionState() {
      const canActOnTask = canCurrentUserActOnTask(activeTaskDetail?.metadata);
      const editableArtifact = Boolean(activeTaskDetail && activeTaskDetail.metadata.state === 'waiting-check-plans' && activeArtifactName === 'PLAN.md' && canActOnTask);
      const canSplitPlan = Boolean(activeTaskDetail && activeTaskDetail.metadata.state === 'waiting-check-plans' && taskHasSplitProposal(activeTaskDetail.metadata) && canActOnTask);
      togglePlanEditButton.hidden = !editableArtifact;
      savePlanButton.hidden = !editableArtifact || !planEditMode;
      approvePlanButton.hidden = !editableArtifact;
      splitPlanButton.hidden = !canSplitPlan;
      togglePlanEditButton.textContent = planEditMode ? translateTask('backToViewer') : translateTask('editPlan');
      savePlanButton.disabled = !editableArtifact || !planEditMode;
      approvePlanButton.disabled = !editableArtifact || taskDetailStale;
      splitPlanButton.disabled = !canSplitPlan || taskDetailStale;
    }

    function updateHumanVerificationState() {
      const state = activeTaskDetail?.metadata?.state;
      const canActOnTask = canCurrentUserActOnTask(activeTaskDetail?.metadata);
      const verificationLeaseRunId = activeTaskDetail?.metadata?.lease?.run_id;
      const review = activeTaskDetail?.metadata?.review || {};
      const integrationApplied = Boolean(activeTaskDetail?.metadata?.integration?.applied);
      const canResumePlanner = canActOnTask && state === 'requests'
        && typeof activeTaskDetail?.metadata?.retry_gate?.reason === 'string'
        && activeTaskDetail.metadata.retry_gate.reason.startsWith('planner-');
      const canResumeImplementer = canActOnTask && canResumeImplementerForMetadata(activeTaskDetail?.metadata, state);
      const canResumeReviewer = canActOnTask && state === 'waiting-reviews'
        && typeof activeTaskDetail?.metadata?.retry_gate?.reason === 'string'
        && activeTaskDetail.metadata.retry_gate.reason.startsWith('review-')
        && Boolean(activeTaskDetail?.metadata?.retry_gate?.not_before);
      const canResumeReviewLoop = canActOnTask && state === 'todos' && review.human_rework_required === true;
      const verificationStartInFlight = state === 'completed-reviews' && verificationLeaseRunId === 'manual-human-verifying';
      const canStart = canActOnTask && state === 'completed-reviews' && !verificationStartInFlight;
      const canApproveOrReject = canActOnTask && state === 'human-verifying';
      const canRetryApply = canApproveOrReject && !integrationApplied;
      resumePlannerButton.hidden = !canResumePlanner;
      resumePlannerButton.disabled = !canResumePlanner || taskDetailStale;
      resumeImplementerButton.hidden = !canResumeImplementer;
      resumeImplementerButton.disabled = !canResumeImplementer || taskDetailStale;
      resumeReviewerButton.hidden = !canResumeReviewer;
      resumeReviewerButton.disabled = !canResumeReviewer || taskDetailStale;
      resumeReviewLoopButton.hidden = !canResumeReviewLoop;
      resumeReviewLoopButton.disabled = !canResumeReviewLoop || taskDetailStale;
      startVerificationButton.hidden = !canStart;
      startVerificationButton.disabled = !canStart || taskDetailStale;
      retryVerificationApplyButton.hidden = !canRetryApply;
      retryVerificationApplyButton.disabled = !canRetryApply || taskDetailStale;
      requestChangesButton.hidden = !canApproveOrReject;
      requestChangesShell.hidden = !canApproveOrReject;
      approveHumanReviewButton.hidden = !canApproveOrReject;
      approveHumanReviewShell.hidden = !canApproveOrReject;
      if (!canApproveOrReject) setApprovalChoiceModalOpen(false);
      if (!canResumePlanner) setResumePlannerChoiceModalOpen(false, { force: true });
      if (!canResumeReviewer) setResumeReviewerChoiceModalOpen(false, { force: true });
      updateHumanReviewPanel();
    }

    function updateTaskDeleteState() {
      const state = activeTaskDetail?.metadata?.state;
      const available = Boolean(state);
      const canActOnTask = canCurrentUserActOnTask(activeTaskDetail?.metadata);
      const canCancel = canActOnTask && available && state !== 'done' && state !== 'closed';
      const canRerequest = canActOnTask && available && state === 'closed' && activeTaskDetail?.metadata?.closure?.reason === 'cancelled_by_human';
      rerequestTaskButton.hidden = !canRerequest;
      rerequestTaskButton.disabled = !canRerequest || taskDetailStale;
      cancelTaskButton.hidden = !canCancel;
      cancelTaskButton.disabled = !canCancel || taskDetailStale;
      deleteTaskButton.hidden = !available || !canActOnTask;
      deleteTaskButton.disabled = !available || !canActOnTask || taskDetailStale;
    }

    function taskVisitText(summary) {
      if (!summary.attempt_count) return translateTask('notVisitedYet');
      const base = translateTask(summary.attempt_count === 1 ? 'visitOne' : 'visitMany', { count: summary.attempt_count });
      return `${base}${summary.is_current ? translateTask('currentSuffix') : ''}`;
    }

    const singleVisitStageLabels = new Set(['requests', 'planning', 'plan-approving', 'waiting-check-plans', 'done', 'closed']);

    function formatStageVisitLabel(segment) {
      const baseLabel = stateLabel(segment.state);
      if (segment.visit_index === 1 && singleVisitStageLabels.has(segment.state)) return baseLabel;
      return `${baseLabel} #${segment.visit_index}`;
    }

    function formatStageSegmentEnd(segment) {
      if (segment.exited_at) return formatDateTime(segment.exited_at);
      if (segment.state === 'done' || segment.state === 'closed') return translateTask('completedLabel');
      return translateTask('now');
    }

    const stageTimingRows = [
      ['requests', 'planning', 'plan-approving', 'waiting-check-plans'],
      ['todos', 'implementing', 'waiting-reviews'],
      ['reviewing', 'completed-reviews', 'human-verifying'],
    ];

    function renderStageTiming(stageTiming) {
      const summaries = Array.isArray(stageTiming?.summaries) ? stageTiming.summaries.filter((summary) => summary.state !== 'done' && summary.state !== 'closed') : [];
      const segments = Array.isArray(stageTiming?.segments) ? stageTiming.segments : [];
      const bucketDurationAttrs = (durationMs, states, { live = true } = {}) => {
        if (!live) return buildDurationAttributes(Number(durationMs || 0));
        const liveSegment = segments.find((segment) => segment.is_current && states.includes(segment.state) && segment.state !== 'done');
        const liveDurationMs = liveSegment ? Number(liveSegment.duration_ms || 0) : 0;
        const baseDurationMs = liveSegment ? Math.max(0, Number(durationMs || 0) - liveDurationMs) : Number(durationMs || 0);
        return buildDurationAttributes(baseDurationMs, liveSegment ? liveSegment.entered_at : '');
      };
      const summaryMap = new Map(summaries.map((summary) => [summary.state, summary]));
      const visitedStates = new Set([
        ...summaries.filter((summary) => Number(summary.attempt_count || 0) > 0 || Number(summary.total_duration_ms || 0) > 0 || summary.is_current).map((summary) => summary.state),
        ...segments.map((segment) => segment.state),
      ]);
      const hasRecordedTime = segments.length > 0 || Array.from(visitedStates).length > 0;
      const hiddenDurationMs = Array.isArray(stageTiming?.segments)
        ? stageTiming.segments
            .filter((segment) => segment.state === 'done' || segment.state === 'closed')
            .reduce((total, segment) => total + Number(segment.duration_ms || 0), 0)
        : 0;
      const totalDurationMs = Math.max(0, Number(stageTiming?.total_duration_ms || 0) - hiddenDurationMs);
      const aiWorkDurationMs = Math.max(0, Number(stageTiming?.ai_work_duration_ms || 0));
      const humanWorkDurationMs = Math.max(0, Number(stageTiming?.human_work_duration_ms || 0));
      const waitingDurationMs = Math.max(0, Number(stageTiming?.waiting_duration_ms || 0));
      const currentSummary = summaries.find((summary) => summary.is_current) || null;
      const currentSummaryIsLive = Boolean(currentSummary && currentSummary.state !== 'done' && currentSummary.state !== 'closed');
      const totalBaseDurationMs = currentSummaryIsLive
        ? Math.max(0, totalDurationMs - Number(currentSummary.latest_duration_ms || 0))
        : totalDurationMs;
      const totalDurationAttrs = buildDurationAttributes(totalBaseDurationMs, currentSummaryIsLive ? currentSummary.latest_entered_at : '', '', translateTask('trackedSuffix'));
      const timingBreakdown = `
        <div class="stage-timing-breakdown">
          <span class="stage-timing-breakdown-item">
            <span>${escapeHtml(translateTask('aiWorkTime'))}</span>
            <strong ${bucketDurationAttrs(aiWorkDurationMs, [], { live: false })}>${formatElapsed(aiWorkDurationMs)}</strong>
          </span>
          <span class="stage-timing-breakdown-separator" aria-hidden="true">|</span>
          <span class="stage-timing-breakdown-item">
            <span>${escapeHtml(translateTask('humanWorkTime'))}</span>
            <strong ${bucketDurationAttrs(humanWorkDurationMs, ['human-verifying'])}>${formatElapsed(humanWorkDurationMs)}</strong>
          </span>
          <span class="stage-timing-breakdown-separator" aria-hidden="true">|</span>
          <span class="stage-timing-breakdown-item">
            <span>${escapeHtml(translateTask('waitingTime'))}</span>
            <strong ${bucketDurationAttrs(waitingDurationMs, ['requests', 'waiting-check-plans', 'todos', 'waiting-reviews', 'completed-reviews'])}>${formatElapsed(waitingDurationMs)}</strong>
          </span>
          <span class="stage-timing-breakdown-separator" aria-hidden="true">|</span>
          <span class="stage-timing-breakdown-item is-total">
            <span>${escapeHtml(translateTask('totalTrackedTime'))}</span>
            <strong ${totalDurationAttrs}>${formatElapsed(totalDurationMs)}${escapeHtml(translateTask('trackedSuffix'))}</strong>
          </span>
        </div>`;
      const summaryCards = stageTimingRows.map((states) => {
        const cards = states.map((state) => {
          const summary = summaryMap.get(state) || {
            state,
            attempt_count: 0,
            total_duration_ms: 0,
            latest_duration_ms: 0,
            latest_entered_at: '',
            is_current: false,
          };
          const summaryIsLive = summary.is_current && summary.state !== 'done' && summary.state !== 'closed';
          const totalBaseMs = summaryIsLive
            ? Math.max(0, Number(summary.total_duration_ms || 0) - Number(summary.latest_duration_ms || 0))
            : Number(summary.total_duration_ms || 0);
          const reached = visitedStates.has(state);
          const cardStateClass = summary.is_current ? ' current' : reached ? ' reached' : ' upcoming';
          return `
            <article class="stage-timing-card${cardStateClass}" style="--stage-color:${stageColor(state)}">
              <span>${escapeHtml(stateLabel(state))}</span>
              <strong ${buildDurationAttributes(totalBaseMs, summaryIsLive ? summary.latest_entered_at : '', translateTask('totalPrefix'))}>${`${translateTask('totalPrefix')}${formatElapsed(summary.total_duration_ms || 0)}`}</strong>
              <small>${escapeHtml(taskVisitText(summary))}</small>
              <small>${summary.latest_entered_at ? escapeHtml(translateTask('latestEntry', { value: formatDateTime(summary.latest_entered_at) })) : escapeHtml(translateTask('noTimeRecorded'))}</small>
            </article>
          `;
        }).join('');
        return `<div class="stage-timing-row" style="--stage-columns:${states.length}">${cards}</div>`;
      }).join('');
      const timelineBar = segments.length
        ? `<div class="stage-timeline-bar">${segments.map((segment) => {
            const ratio = totalDurationMs > 0 ? (Number(segment.duration_ms || 0) / totalDurationMs) * 100 : 100 / segments.length;
            const flexBasis = Math.max(ratio, 4);
            const title = `${formatStageVisitLabel(segment)} - ${formatElapsed(segment.duration_ms || 0)}`;
            return `<div class="stage-timeline-segment${segment.is_current ? ' current' : ''}" style="--stage-color:${stageColor(segment.state)}; flex: ${flexBasis} 1 0%;" title="${escapeHtml(title)}"></div>`;
          }).join('')}</div>`
        : `<div class="muted">${escapeHtml(translateTask('noStageTransitions'))}</div>`;
      const segmentRows = segments.length
        ? `<div class="stage-segment-list">${segments.map((segment) => `
            <div class="stage-segment-row">
              <span class="stage-swatch" style="--stage-color:${stageColor(segment.state)}"></span>
              <strong>${escapeHtml(formatStageVisitLabel(segment))}</strong>
              <span class="stage-segment-duration" ${buildDurationAttributes(segment.is_current && segment.state !== 'done' ? 0 : Number(segment.duration_ms || 0), segment.is_current && segment.state !== 'done' ? segment.entered_at : '')}>${formatElapsed(segment.duration_ms || 0)}</span>
              <span class="muted">${escapeHtml(formatDateTime(segment.entered_at))} -> ${escapeHtml(formatStageSegmentEnd(segment))}</span>
            </div>
          `).join('')}</div>`
        : `<div class="muted">${escapeHtml(translateTask('noStageVisits'))}</div>`;
      return `
        <div class="task-section">
          <div class="stage-timing-shell">
            <div class="stage-timing-head">
              <h3>${escapeHtml(translateTask('stageTiming'))}</h3>
              <div class="stage-timing-total">
                ${timingBreakdown}
              </div>
            </div>
            <div class="stage-timing-grid">${summaryCards}</div>
            ${hasRecordedTime ? '' : `<div class="muted">${escapeHtml(translateTask('noStateHistory'))}</div>`}
            ${timelineBar}
            ${segmentRows}
          </div>
        </div>
      `;
    }

    function latestVisibleError(errors) {
      if (!Array.isArray(errors)) return null;
      return [...errors].reverse().find((item) => item && item.code !== 'human-verification-rejected') || null;
    }

    function effectiveCompletedGroup(metadata) {
      const override = (metadata?.completed_group_override || '').trim();
      return override || metadata?.target?.base_branch || '';
    }

    function renderCompletedGroupSection(detail) {
      const metadata = detail?.metadata;
      if (metadata?.state !== 'done') return '';
      const override = metadata.completed_group_override || '';
      const baseBranch = metadata.target?.base_branch || '';
      const currentGroup = effectiveCompletedGroup(metadata) || 'unknown';
      return `
        <div class="task-section">
          <h3>${escapeHtml(translateTask('completedGroupTitle'))}</h3>
          <div class="settings-card">
            <div class="settings-copy">
              <strong>${escapeHtml(translateTask('completedGroupTitle'))}</strong>
              <p>${escapeHtml(translateTask('completedGroupDescription'))}</p>
            </div>
            <div class="field">
              <label for="completed-group-input">${escapeHtml(translateTask('completedGroupInputLabel'))}</label>
              <input id="completed-group-input" type="text" value="${escapeHtml(override)}" placeholder="${escapeHtml(translateTask('completedGroupInputPlaceholder'))}" ${taskDetailStale ? 'disabled' : ''}>
            </div>
            <div class="card-tag-row">
              <span class="card-tag card-tag-branch">${escapeHtml(translateTask('completedGroupCurrentLabel'))}: <span class="card-tag-value">${escapeHtml(currentGroup)}</span></span>
              <span class="card-tag card-tag-branch">${escapeHtml(translateTask('completedGroupDefaultLabel'))}: <span class="card-tag-value">${escapeHtml(baseBranch || 'unknown')}</span></span>
            </div>
            <div class="header-actions">
              <button type="button" data-action="save-completed-group" ${taskDetailStale ? 'disabled' : ''}>${escapeHtml(translateTask('completedGroupSave'))}</button>
              <button type="button" data-action="clear-completed-group" class="ghost-button" ${taskDetailStale || !override.trim() ? 'disabled' : ''}>${escapeHtml(translateTask('completedGroupClear'))}</button>
            </div>
            <div id="completed-group-status" class="muted"></div>
          </div>
        </div>
      `;
    }

    function renderRemoteCompletionSection(detail) {
      const metadata = detail?.metadata;
      if (metadata?.state !== 'done') return '';
      const integration = metadata.integration || {};
      const branch = integration.final_remote_branch || '';
      const url = integration.remote_merge_request_url || '';
      if (!branch && !url) return '';
      return `
        <div class="task-section">
          <h3>${escapeHtml(translateTask('remoteCompletionTitle'))}</h3>
          <div class="settings-card remote-completion-card">
            <div class="remote-completion-header">
              <div class="remote-completion-icon">
                <svg class="success-icon" viewBox="0 0 20 20" fill="currentColor">
                  <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clip-rule="evenodd" />
                </svg>
              </div>
              <div class="remote-completion-copy">
                <strong>${escapeHtml(translateTask('remoteCompletionTitle'))}</strong>
                <p>${escapeHtml(translateTask('remoteCompletionDescription'))}</p>
              </div>
            </div>
            <div class="remote-completion-body">
              ${branch ? `
                <div class="remote-completion-branch-wrapper">
                  <svg class="branch-icon" viewBox="0 0 16 16" fill="currentColor">
                    <path fill-rule="evenodd" d="M11.5 7.5a1.5 1.5 0 100 3 1.5 1.5 0 000-3zM9 13a1 1 0 011-1h1.5a2.5 2.5 0 002.5-2.5V5.707a1 1 0 00-.293-.707l-2-2A1 1 0 0011.293 3H9.5a1.5 1.5 0 00-3 0H4.5A2.5 2.5 0 002 5.5v3.293a1 1 0 00.293.707l2 2A1 1 0 005.293 12H7a1 1 0 011 1zm-3-8.5a.5.5 0 11-1 0 .5.5 0 011 0z" />
                  </svg>
                  <span class="branch-label">${escapeHtml(translateTask('remoteCompletionBranch'))}:</span>
                  <span class="branch-name">${escapeHtml(branch)}</span>
                </div>
              ` : ''}
              ${url ? `
                <a class="accent-button remote-completion-link" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer" style="color: #ffffff !important; text-decoration: none !important; display: inline-flex !important; align-items: center; justify-content: center; gap: 8px;">
                  <span style="color: #ffffff !important; font-weight: 600;">${escapeHtml(translateTask('remoteCompletionAction'))}</span>
                  <svg class="external-link-icon" viewBox="0 0 20 20" fill="currentColor">
                    <path d="M11 3a1 1 0 100 2h2.586l-6.293 6.293a1 1 0 101.414 1.414L15 6.414V9a1 1 0 102 0V4a1 1 0 00-1-1h-5z" />
                    <path d="M5 5a2 2 0 00-2 2v8a2 2 0 002 2h8a2 2 0 002-2v-3a1 1 0 10-2 0v3H5V7h3a1 1 0 000-2H5z" />
                  </svg>
                </a>
              ` : `<div class="muted">${escapeHtml(translateTask('remoteCompletionNoUrl'))}</div>`}
            </div>
          </div>
        </div>
      `;
    }

    function completedGroupInput() {
      return document.getElementById('completed-group-input');
    }

    function updateCompletedGroupControls() {
      const metadata = activeTaskDetail?.metadata;
      const input = completedGroupInput();
      const saveButton = taskOverview.querySelector('[data-action="save-completed-group"]');
      const clearButton = taskOverview.querySelector('[data-action="clear-completed-group"]');
      const status = document.getElementById('completed-group-status');
      if (!metadata || metadata.state !== 'done' || !input || !saveButton || !clearButton || !status) return;
      const savedOverride = (metadata.completed_group_override || '').trim();
      const inputValue = (input.value || '').trim();
      saveButton.disabled = taskDetailStale || inputValue === savedOverride;
      clearButton.disabled = taskDetailStale || !savedOverride;
      status.textContent = taskDetailStale ? translateTask('completedGroupBlockedStale') : '';
    }

    async function saveCompletedGroupOverride(nextGroup) {
      if (!activeTaskId || activeTaskDetail?.metadata?.state !== 'done') return;
      const status = document.getElementById('completed-group-status');
      if (status) status.textContent = translateTask('completedGroupSaving');
      const response = await fetch(`/api/tasks/${activeTaskId}/completed-group`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ group: nextGroup }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || translateTask('completedGroupSaveFailed'));
      await loadTaskDetails(activeTaskId);
      const refreshedGroup = effectiveCompletedGroup(activeTaskDetail?.metadata) || 'unknown';
      const refreshedStatus = document.getElementById('completed-group-status');
      if (refreshedStatus) {
        refreshedStatus.textContent = nextGroup
          ? translateTask('completedGroupSavedOverride', { group: refreshedGroup })
          : translateTask('completedGroupSavedDefault', { group: refreshedGroup });
      }
      updateCompletedGroupControls();
    }

    function renderTaskOverview(detail) {
      const metadata = detail.metadata;
      const qaScrollState = activeTaskTab === 'qa-checklist' ? captureQaChecklistScrollState() : null;
      activeTaskDetail = detail;
      setTaskDetailStale(false);
      const latestError = latestVisibleError(metadata.errors);
      const changedFilesVisible = metadata.state !== 'done' && Boolean(detail.changed_files_available || detail.changed_files.length > 0);
      const qaChecklistVisible = metadata.state === 'completed-reviews' || metadata.state === 'human-verifying';
      const reviewerQaVisible = metadata.state === 'completed-reviews' || metadata.state === 'human-verifying';
      const reviewNoteVisible = metadata.state === 'human-verifying';
      const viewerVisible = detail.markdown_files.length > 0;
      const logsVisible = detail.log_files.length > 0 || ['planning', 'implementing', 'reviewing'].includes(metadata.state);
      const planEditable = metadata.state === 'waiting-check-plans' && detail.markdown_files.includes('PLAN.md');
      taskTabLogs.hidden = !logsVisible;
      taskTabChangedFiles.hidden = !changedFilesVisible;
      taskTabQaChecklist.hidden = !qaChecklistVisible;
      taskTabReviewerQa.hidden = !reviewerQaVisible;
      taskTabReviewNote.hidden = !reviewNoteVisible;
      taskTabEditor.hidden = !viewerVisible;
      if (!logsVisible && taskTabLogs.classList.contains('active')) setTaskTab(viewerVisible ? 'editor' : 'overview');
      if (!changedFilesVisible && taskTabChangedFiles.classList.contains('active')) setTaskTab('overview');
      if (!qaChecklistVisible && taskTabQaChecklist.classList.contains('active')) setTaskTab(changedFilesVisible ? 'changed-files' : 'overview');
      if (!reviewerQaVisible && taskTabReviewerQa.classList.contains('active')) setTaskTab(changedFilesVisible ? 'changed-files' : 'overview');
      if (!reviewNoteVisible && taskTabReviewNote.classList.contains('active')) setTaskTab(changedFilesVisible ? 'changed-files' : 'overview');
      if (!viewerVisible && taskTabEditor.classList.contains('active')) setTaskTab('overview');
      if (planEditable && activeArtifactName !== 'PLAN.md') activeArtifactName = 'PLAN.md';
      if (detail.changed_files.length && (!activeChangedFileId || !detail.changed_files.some((file) => file.id === activeChangedFileId))) activeChangedFileId = detail.changed_files[0]?.id || null;
      if (!activeArtifactName || !detail.markdown_files.includes(activeArtifactName)) activeArtifactName = preferredArtifact(detail.markdown_files, metadata);
      planEditMode = false;
      taskModeBadge.textContent = translateTask('viewerMode');
      setTaskEditorMessage('');
      updatePlanActionState();
      updateHumanVerificationState();
      updateTaskDeleteState();
      renderQaChecklistPanel({ scrollState: qaScrollState });
      setReviewerQaTranscript(detail.human_review?.reviewer_qa_markdown || '', { preserveScroll: true });
      setHumanReviewEditorContent(detail.human_review?.note_markdown || '');
      updateReviewerQaPanel();
      if (detail.changed_files.length || !detail.changed_files_available) {
        activeChangedFileDetail = null;
        renderChangedFileButtons(detail.changed_files);
        renderDiffPlaceholder(detail.changed_files.length ? translateTask('selectChangedFile') : translateTask('changedFilesPatchAvailable'));
      }
      renderArtifactButtons(detail.markdown_files);
      saveBoardScrollPositions();
      taskOverview.innerHTML = `
        ${renderStageTiming(detail.stage_timing)}
        <div class="task-section">
          <h3>${escapeHtml(translateTask('latestError'))}</h3>
          <div class="muted">${latestError ? escapeHtml(latestError.message) : escapeHtml(translateTask('noRecordedErrors'))}</div>
        </div>
        ${renderRemoteCompletionSection(detail)}
        ${renderCompletedGroupSection(detail)}
      `;
      document.getElementById('task-modal-title').textContent = metadata.title;
      document.getElementById('task-modal-subtitle').innerHTML = renderTaskSubtitleTags({
        task_id: metadata.task_id,
        state: metadata.state,
        created_by_user_id: metadata.created_by_user_id,
        created_by_username: metadata.created_by_username,
        target_repo_root: metadata.target.repo_root,
        target_repo_label: metadata.target.repo_label,
        base_branch: metadata.target.base_branch,
        final_branch: metadata.integration.final_branch || '',
        stage_timing: detail.stage_timing,
        history: metadata.history || [],
      });
      updateCompletedGroupControls();
      restoreBoardScrollPositions();
      scheduleQaChecklistScrollRestore(qaScrollState);
    }
