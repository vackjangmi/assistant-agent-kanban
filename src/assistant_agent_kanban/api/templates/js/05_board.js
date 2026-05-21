
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
        });
      } else if (noteExists || currentCommentCount > 0) {
        setApprovalGateNotice({
          title: translateHumanReview('approvalGateReviewTitle'),
          body: translateHumanReview('approvalGateReviewBody'),
        });
      } else if (incompleteQaCount > 0) {
        setApprovalGateNotice({
          title: translateHumanReview('approvalGateQaTitle'),
          body: translateHumanReview(incompleteQaCount === 1 ? 'approvalGateQaBodyOne' : 'approvalGateQaBodyMany', { count: incompleteQaCount }),
          actionLabel: translateHumanReview('approvalGateQaAction'),
          action: 'qa-checklist',
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
      const editableArtifact = Boolean(activeTaskDetail && activeTaskDetail.metadata.state === 'waiting-check-plans' && activeArtifactName === 'PLAN.md');
      togglePlanEditButton.hidden = !editableArtifact;
      savePlanButton.hidden = !editableArtifact || !planEditMode;
      approvePlanButton.hidden = !editableArtifact;
      togglePlanEditButton.textContent = planEditMode ? translateTask('backToViewer') : translateTask('editPlan');
      savePlanButton.disabled = !editableArtifact || !planEditMode;
       approvePlanButton.disabled = !editableArtifact || taskDetailStale;
    }

    function updateHumanVerificationState() {
      const state = activeTaskDetail?.metadata?.state;
      const verificationLeaseRunId = activeTaskDetail?.metadata?.lease?.run_id;
      const review = activeTaskDetail?.metadata?.review || {};
      const integrationApplied = Boolean(activeTaskDetail?.metadata?.integration?.applied);
      const canResumePlanner = state === 'requests'
        && typeof activeTaskDetail?.metadata?.retry_gate?.reason === 'string'
        && activeTaskDetail.metadata.retry_gate.reason.startsWith('planner-');
      const canResumeImplementer = canResumeImplementerForMetadata(activeTaskDetail?.metadata, state);
      const canResumeReviewer = state === 'waiting-reviews'
        && typeof activeTaskDetail?.metadata?.retry_gate?.reason === 'string'
        && activeTaskDetail.metadata.retry_gate.reason.startsWith('review-')
        && Boolean(activeTaskDetail?.metadata?.retry_gate?.not_before);
      const canResumeReviewLoop = state === 'todos' && review.human_rework_required === true;
      const verificationStartInFlight = state === 'completed-reviews' && verificationLeaseRunId === 'manual-human-verifying';
      const canStart = state === 'completed-reviews' && !verificationStartInFlight;
      const canApproveOrReject = state === 'human-verifying';
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
      deleteTaskButton.hidden = !available;
      deleteTaskButton.disabled = !available || taskDetailStale;
    }

    function taskVisitText(summary) {
      if (!summary.attempt_count) return translateTask('notVisitedYet');
      const base = translateTask(summary.attempt_count === 1 ? 'visitOne' : 'visitMany', { count: summary.attempt_count });
      return `${base}${summary.is_current ? translateTask('currentSuffix') : ''}`;
    }

    const singleVisitStageLabels = new Set(['requests', 'planning', 'plan-approving', 'waiting-check-plans', 'done']);

    function formatStageVisitLabel(segment) {
      const baseLabel = stateLabel(segment.state);
      if (segment.visit_index === 1 && singleVisitStageLabels.has(segment.state)) return baseLabel;
      return `${baseLabel} #${segment.visit_index}`;
    }

    function formatStageSegmentEnd(segment) {
      if (segment.exited_at) return formatDateTime(segment.exited_at);
      if (segment.state === 'done') return translateTask('completedLabel');
      return translateTask('now');
    }

    const stageTimingRows = [
      ['requests', 'planning', 'plan-approving', 'waiting-check-plans'],
      ['todos', 'implementing', 'waiting-reviews'],
      ['reviewing', 'completed-reviews', 'human-verifying'],
    ];

    function renderStageTiming(stageTiming) {
      const summaries = Array.isArray(stageTiming?.summaries) ? stageTiming.summaries.filter((summary) => summary.state !== 'done') : [];
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
            .filter((segment) => segment.state === 'done')
            .reduce((total, segment) => total + Number(segment.duration_ms || 0), 0)
        : 0;
      const totalDurationMs = Math.max(0, Number(stageTiming?.total_duration_ms || 0) - hiddenDurationMs);
      const aiWorkDurationMs = Math.max(0, Number(stageTiming?.ai_work_duration_ms || 0));
      const humanWorkDurationMs = Math.max(0, Number(stageTiming?.human_work_duration_ms || 0));
      const waitingDurationMs = Math.max(0, Number(stageTiming?.waiting_duration_ms || 0));
      const currentSummary = summaries.find((summary) => summary.is_current) || null;
      const currentSummaryIsLive = Boolean(currentSummary && currentSummary.state !== 'done');
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
          const summaryIsLive = summary.is_current && summary.state !== 'done';
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
      activeTaskDetail = detail;
      setTaskDetailStale(false);
      const latestError = latestVisibleError(metadata.errors);
      const changedFilesVisible = Boolean(detail.changed_files_available || detail.changed_files.length > 0);
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
      renderQaChecklistPanel();
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
        ${renderCompletedGroupSection(detail)}
      `;
      document.getElementById('task-modal-title').textContent = metadata.title;
      document.getElementById('task-modal-subtitle').innerHTML = renderTaskSubtitleTags({
        task_id: metadata.task_id,
        state: metadata.state,
        target_repo_root: metadata.target.repo_root,
        target_repo_label: metadata.target.repo_label,
        base_branch: metadata.target.base_branch,
        final_branch: metadata.integration.final_branch || '',
        stage_timing: detail.stage_timing,
        history: metadata.history || [],
      });
      updateCompletedGroupControls();
      restoreBoardScrollPositions();
    }

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
        return;
      }
      const previousMax = Math.max(0, state.scrollHeight - taskLogViewer.clientHeight);
      const relativeOffset = previousMax - state.scrollTop;
      taskLogViewer.scrollTop = Math.max(0, nextMax - relativeOffset);
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

    async function loadMarkdownArtifact(taskId, filename = null) {
      if (!activeTaskDetail || !activeTaskDetail.markdown_files.length) return;
      const resolvedArtifactName = filename && activeTaskDetail.markdown_files.includes(filename) ? filename : preferredArtifact(activeTaskDetail.markdown_files);
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
        requestAnimationFrame(resetArtifactViewerScroll);
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

    function openResumeImplementerChoiceModal() {
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'todos') return;
      if (!canResumeImplementerForMetadata(activeTaskDetail.metadata, activeTaskDetail.metadata.state)) return;
      if (resumeImplementerButton.disabled) return;
      taskModalError.hidden = true;
      taskModalError.textContent = '';
      setResumeImplementerChoiceModalOpen(true);
    }

    function openResumePlannerChoiceModal() {
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'requests') return;
      if (typeof activeTaskDetail.metadata.retry_gate?.reason !== 'string') return;
      if (!activeTaskDetail.metadata.retry_gate.reason.startsWith('planner-')) return;
      if (resumePlannerButton.disabled) return;
      taskModalError.hidden = true;
      taskModalError.textContent = '';
      setResumePlannerChoiceModalOpen(true);
    }

    function openResumeReviewerChoiceModal() {
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'waiting-reviews') return;
      if (typeof activeTaskDetail.metadata.retry_gate?.reason !== 'string') return;
      if (!activeTaskDetail.metadata.retry_gate.reason.startsWith('review-')) return;
      if (!activeTaskDetail.metadata.retry_gate?.not_before) return;
      if (resumeReviewerButton.disabled) return;
      taskModalError.hidden = true;
      taskModalError.textContent = '';
      setResumeReviewerChoiceModalOpen(true);
    }

    function canResumeImplementerForMetadata(metadata, state) {
      if (state !== 'todos') return false;
      const retryReason = typeof metadata?.retry_gate?.reason === 'string' ? metadata.retry_gate.reason : '';
      if (!retryReason || !metadata?.retry_gate?.not_before) return false;
      if (retryReason.startsWith('implementation-')) return true;
      return retryReason === 'review-rework-backstop' && metadata?.review?.human_rework_required !== true;
    }

    async function resumeImplementer(resumeMode) {
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'todos') return;
      if (!canResumeImplementerForMetadata(activeTaskDetail.metadata, activeTaskDetail.metadata.state)) return;
      const normalizedResumeMode = resumeMode === 'current-settings' ? 'current-settings' : 'pinned';
      const message = (resumeImplementerMessageInput.value || '').trim();
      const statusKey = normalizedResumeMode === 'current-settings' ? 'resumeImplementerSubmittingCurrent' : 'resumeImplementerSubmittingPinned';
      resumeImplementerSubmissionInFlight = true;
      resumeImplementerButton.disabled = true;
      resumeImplementerChoicePinnedButton.disabled = true;
      resumeImplementerChoiceCurrentButton.disabled = true;
      closeResumeImplementerChoiceButton.disabled = true;
      resumeImplementerChoiceStatus.hidden = false;
      resumeImplementerChoiceStatus.dataset.tone = 'neutral';
      resumeImplementerChoiceStatus.textContent = translateTask(statusKey);
      try {
        const response = await fetch(`/api/tasks/${activeTaskId}/resume-implementer`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ resume_mode: normalizedResumeMode, message }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateTask('failedResumeImplementer'));
        await loadBoard();
        resumeImplementerSubmissionInFlight = false;
        setResumeImplementerChoiceModalOpen(false, { force: true });
        await loadTaskDetail(activeTaskId, true);
      } catch (error) {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        resumeImplementerChoiceStatus.hidden = false;
        resumeImplementerChoiceStatus.dataset.tone = 'error';
        resumeImplementerChoiceStatus.textContent = error.message;
      } finally {
        resumeImplementerSubmissionInFlight = false;
        if (!resumeImplementerChoiceModal.hidden) {
          resumeImplementerChoicePinnedButton.disabled = false;
          resumeImplementerChoiceCurrentButton.disabled = false;
          closeResumeImplementerChoiceButton.disabled = false;
        }
        updateHumanVerificationState();
      }
    }

    async function resumePlanner() {
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'requests') return;
      if (typeof activeTaskDetail.metadata.retry_gate?.reason !== 'string') return;
      if (!activeTaskDetail.metadata.retry_gate.reason.startsWith('planner-')) return;
      const message = (resumePlannerMessageInput.value || '').trim();
      resumePlannerSubmissionInFlight = true;
      resumePlannerButton.disabled = true;
      resumePlannerChoiceButton.disabled = true;
      closeResumePlannerChoiceButton.disabled = true;
      resumePlannerChoiceStatus.hidden = false;
      resumePlannerChoiceStatus.dataset.tone = 'neutral';
      resumePlannerChoiceStatus.textContent = translateTask('resumePlannerSubmitting');
      try {
        const response = await fetch(`/api/tasks/${activeTaskId}/resume-planner`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateTask('failedResumePlanner'));
        await loadBoard();
        resumePlannerSubmissionInFlight = false;
        setResumePlannerChoiceModalOpen(false, { force: true });
        await loadTaskDetail(activeTaskId, true);
      } catch (error) {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        resumePlannerChoiceStatus.hidden = false;
        resumePlannerChoiceStatus.dataset.tone = 'error';
        resumePlannerChoiceStatus.textContent = error.message;
      } finally {
        resumePlannerSubmissionInFlight = false;
        if (!resumePlannerChoiceModal.hidden) {
          resumePlannerChoiceButton.disabled = false;
          closeResumePlannerChoiceButton.disabled = false;
        }
        updateHumanVerificationState();
      }
    }

    async function resumeReviewer(resumeMode) {
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'waiting-reviews') return;
      if (typeof activeTaskDetail.metadata.retry_gate?.reason !== 'string') return;
      if (!activeTaskDetail.metadata.retry_gate.reason.startsWith('review-')) return;
      if (!activeTaskDetail.metadata.retry_gate?.not_before) return;
      const normalizedResumeMode = resumeMode === 'current-settings' ? 'current-settings' : 'pinned';
      const message = (resumeReviewerMessageInput.value || '').trim();
      const statusKey = normalizedResumeMode === 'current-settings' ? 'resumeReviewerSubmittingCurrent' : 'resumeReviewerSubmittingPinned';
      resumeReviewerSubmissionInFlight = true;
      resumeReviewerButton.disabled = true;
      resumeReviewerChoicePinnedButton.disabled = true;
      resumeReviewerChoiceCurrentButton.disabled = true;
      closeResumeReviewerChoiceButton.disabled = true;
      resumeReviewerChoiceStatus.hidden = false;
      resumeReviewerChoiceStatus.dataset.tone = 'neutral';
      resumeReviewerChoiceStatus.textContent = translateTask(statusKey);
      try {
        const response = await fetch(`/api/tasks/${activeTaskId}/resume-reviewer`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ resume_mode: normalizedResumeMode, message }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateTask('failedResumeReviewer'));
        await loadBoard();
        resumeReviewerSubmissionInFlight = false;
        setResumeReviewerChoiceModalOpen(false, { force: true });
        await loadTaskDetail(activeTaskId, true);
      } catch (error) {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        resumeReviewerChoiceStatus.hidden = false;
        resumeReviewerChoiceStatus.dataset.tone = 'error';
        resumeReviewerChoiceStatus.textContent = error.message;
      } finally {
        resumeReviewerSubmissionInFlight = false;
        if (!resumeReviewerChoiceModal.hidden) {
          resumeReviewerChoicePinnedButton.disabled = false;
          resumeReviewerChoiceCurrentButton.disabled = false;
          closeResumeReviewerChoiceButton.disabled = false;
        }
        updateHumanVerificationState();
      }
    }

    async function resumeReviewLoop() {
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'todos' || activeTaskDetail.metadata.review?.human_rework_required !== true) return;
      resumeReviewLoopButton.disabled = true;
      try {
        const response = await fetch(`/api/tasks/${activeTaskId}/resume-review-loop`, { method: 'POST' });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateTask('failedResumeReviewLoop'));
        await loadBoard();
        await loadTaskDetail(activeTaskId, true);
      } catch (error) {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
      } finally {
        updateHumanVerificationState();
      }
    }

    async function rejectVerification() {
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'human-verifying') return;
      setApprovalChoiceModalOpen(false);
      requestChangesButton.disabled = true;
      approveHumanReviewButton.disabled = true;
      taskHumanReviewNoteStatus.textContent = translateHumanReview('requestingChanges');
      try {
        await saveHumanReviewNoteIfNeeded();
        const response = await fetch(`/api/tasks/${activeTaskId}/reject-verification`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ note: getHumanReviewEditorContent() }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateTask('failedRejectVerification'));
        await loadBoard();
        setTaskModalOpen(false);
      } catch (error) {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        taskHumanReviewNoteStatus.textContent = error.message;
      } finally {
        updateHumanVerificationState();
      }
    }

    function openApprovalChoiceModal() {
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'human-verifying') return;
      if (approveHumanReviewButton.disabled) return;
      taskModalError.hidden = true;
      taskModalError.textContent = '';
      setApprovalChoiceModalOpen(true);
    }

    async function approveVerification(completionMode) {
      if (!activeTaskId || !activeTaskDetail || activeTaskDetail.metadata.state !== 'human-verifying') return;
      approvalSubmissionInFlight = true;
      requestChangesButton.disabled = true;
      approveHumanReviewButton.disabled = true;
      approvalChoiceTargetButton.disabled = true;
      approvalChoiceNewBranchButton.disabled = true;
      closeApprovalChoiceButton.disabled = true;
      approvalChoiceStatus.hidden = false;
      approvalChoiceStatus.dataset.tone = 'neutral';
      const approvalMessage = translateHumanReview('approving');
      approvalChoiceStatus.textContent = approvalMessage;
      taskHumanReviewNoteStatus.textContent = approvalMessage;
      try {
        await saveHumanReviewNoteIfNeeded();
        const response = await fetch(`/api/tasks/${activeTaskId}/approve-verification`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ completion_mode: completionMode || 'new-branch' }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateTask('failedApproveVerification'));
        approvalSubmissionInFlight = false;
        await loadBoard();
        setApprovalChoiceModalOpen(false, { force: true });
        setTaskModalOpen(false);
      } catch (error) {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        taskHumanReviewNoteStatus.textContent = error.message;
        approvalChoiceStatus.hidden = false;
        approvalChoiceStatus.dataset.tone = 'error';
        approvalChoiceStatus.textContent = error.message;
      } finally {
        approvalSubmissionInFlight = false;
        updateHumanVerificationState();
        if (!approvalChoiceModal.hidden) {
          approvalChoiceTargetButton.disabled = false;
          approvalChoiceNewBranchButton.disabled = false;
          closeApprovalChoiceButton.disabled = false;
        }
      }
    }

    function validateForm() {
      syncRequestGoalField();
      const data = new FormData(requestForm);
      const errors = {};
      const title = (data.get('title') || '').toString().trim();
      const goal = (data.get('goal') || '').toString().trim();
      const targetRepo = normalizeRepoPath(data.get('target_repo'));
      const baseBranch = (data.get('base_branch') || '').toString().trim();
      if (title.length < 5) errors.title = translateRequest('validationTitle');
      if (!goal) errors.goal = translateRequest('validationGoal');
      if (!targetRepo) errors.target_repo = translateRequest('validationTargetRepo');
      if (!baseBranch) errors.base_branch = translateRequest('validationBaseBranch');
      document.querySelectorAll('[data-error-for]').forEach((node) => {
        node.textContent = errors[node.dataset.errorFor] || '';
      });
      return { valid: Object.keys(errors).length === 0, errors };
    }

    async function submitRequest(event) {
      event.preventDefault();
      clearMessages();
      applyRepoDefaults();
      syncRequestGoalField();
      const validation = validateForm();
      if (!validation.valid) {
        const firstInvalidField = ['title', 'target_repo', 'base_branch', 'background', 'goal', 'constraints', 'acceptance_criteria', 'scope', 'out_of_scope', 'references'].find((fieldName) => validation.errors[fieldName]);
        if (firstInvalidField) {
          const lowerComposerFields = new Set(['background', 'goal', 'constraints', 'acceptance_criteria', 'scope', 'out_of_scope', 'references']);
          if (lowerComposerFields.has(firstInvalidField)) setRequestComposerTab('fields');
          requestAnimationFrame(() => focusRequestFieldForValidation(firstInvalidField));
        }
        return;
      }
      const payload = Object.fromEntries(new FormData(requestForm).entries());
      payload.plan_auto_approve = document.getElementById('plan_auto_approve').checked;
      payload.request_upload_token = requestUploadToken;
      payload.request_draft_id = requestDraftId || null;
      submitButton.disabled = true;
      submitButton.textContent = translateRequest('creating');
      try {
        await syncRequestComposerDraftState({ immediate: true, silent: true });
        payload.request_draft_id = requestDraftId || null;
        const response = await fetch('/api/requests', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Request creation failed.');
        persistLastTargetRepo(payload.target_repo);
        clearRequestComposerDraftState();
        requestDraftId = '';
        resetFormState();
        setModalOpen(false);
        renderRequestDrafts();
        boardPhaseManuallySelected = true;
        activeBoardPhase = 'plan';
        await loadBoard();
      } catch (error) {
        formError.hidden = false;
        formError.textContent = error.message;
      } finally {
        submitButton.disabled = false;
        submitButton.textContent = translateRequest('submit');
      }
    }

    document.getElementById('refresh').addEventListener('click', loadBoard);
    openComposerButton.addEventListener('click', async () => {
      clearMessages();
      applyRequestTranslations();
      if (!await restoreRequestComposerDraftState()) resetFormState({ clearSavedDraft: false });
      setModalOpen(true);
      await loadTargetRepoBranches();
    });
    requestDraftsGrid.addEventListener('click', (event) => {
      const openButton = event.target.closest('[data-request-draft-open]');
      if (openButton) {
        void openRequestDraftFromList(openButton.dataset.requestDraftOpen || '');
        return;
      }
      const deleteButton = event.target.closest('[data-request-draft-delete]');
      if (deleteButton) void deleteRequestDraftFromList(deleteButton.dataset.requestDraftDelete || '');
    });
    openSettingsButton.addEventListener('click', openSettingsModal);
    runtimeLanguageInput.addEventListener('change', () => { applyRuntimeSettingsTranslations(); applyRequestTranslations(); applyHumanReviewTranslations(); applyTaskTranslations(); if (activeTaskDetail) renderTaskOverview(activeTaskDetail); refreshRequestDerivedText(); });
    cancelComposerButton.addEventListener('click', () => { clearMessages(); void syncRequestComposerDraftState({ immediate: true, silent: true }); setModalOpen(false); });
    cancelSettingsButton.addEventListener('click', () => closeSettingsModal({ restore: true }));
    closeTaskModalButton.addEventListener('click', () => { setTaskModalOpen(false); });
    closeRetrospectiveModalButton.addEventListener('click', () => { setRetrospectiveModalOpen(false); });
    closeApprovalChoiceButton.addEventListener('click', () => { setApprovalChoiceModalOpen(false); });
    closeResumePlannerChoiceButton.addEventListener('click', () => { setResumePlannerChoiceModalOpen(false); });
    closeResumeImplementerChoiceButton.addEventListener('click', () => { setResumeImplementerChoiceModalOpen(false); });
    closeResumeReviewerChoiceButton.addEventListener('click', () => { setResumeReviewerChoiceModalOpen(false); });
    retrospectiveCompareBranchInput.addEventListener('input', () => {
      activeRetrospectiveComparisonBranch = normalizedRetrospectiveComparisonBranch();
      retrospectiveContextRow.innerHTML = renderRetrospectiveContextTags();
    });
    retrospectiveCreateTargetButton.addEventListener('click', () => createRetrospective('target-branch').catch((error) => { retrospectiveStatus.dataset.tone = 'error'; retrospectiveStatus.textContent = error.message; updateRetrospectiveButtons(activeRetrospectiveRecord || {}); }));
    retrospectiveCreateBranchButton.addEventListener('click', () => createRetrospective('new-branch').catch((error) => { retrospectiveStatus.dataset.tone = 'error'; retrospectiveStatus.textContent = error.message; updateRetrospectiveButtons(activeRetrospectiveRecord || {}); }));
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !modal.hidden) { clearMessages(); void syncRequestComposerDraftState({ immediate: true, silent: true }); setModalOpen(false); } if (event.key === 'Escape' && !settingsModal.hidden) closeSettingsModal({ restore: true }); if (event.key === 'Escape' && !approvalChoiceModal.hidden) { if (approvalSubmissionInFlight) return; setApprovalChoiceModalOpen(false); } else if (event.key === 'Escape' && !resumePlannerChoiceModal.hidden) { if (resumePlannerSubmissionInFlight) return; setResumePlannerChoiceModalOpen(false); } else if (event.key === 'Escape' && !resumeImplementerChoiceModal.hidden) { if (resumeImplementerSubmissionInFlight) return; setResumeImplementerChoiceModalOpen(false); } else if (event.key === 'Escape' && !resumeReviewerChoiceModal.hidden) { if (resumeReviewerSubmissionInFlight) return; setResumeReviewerChoiceModalOpen(false); } else if (event.key === 'Escape' && !taskModal.hidden) { setTaskModalOpen(false); } if (event.key === 'Escape' && !retrospectiveModal.hidden) { setRetrospectiveModalOpen(false); } });
    requestForm.addEventListener('submit', submitRequest);
    requestForm.addEventListener('input', () => void syncRequestComposerDraftState({ silent: true }));
    requestForm.addEventListener('change', () => void syncRequestComposerDraftState({ silent: true }));
    requestDraftInput.addEventListener('input', () => { updateRequestDraftPanel(); void syncRequestComposerDraftState({ silent: true }); });
    requestDraftInput.addEventListener('paste', (event) => {
      const imageFiles = requestDraftClipboardImageFiles(event);
      if (!imageFiles.length) return;
      event.preventDefault();
      attachImagesToRequestDraft(imageFiles).catch((error) => {
        setRequestDraftAttachmentStatusMessage(error.message || translateRequest('draftAttachmentFailed'), 'error');
      });
    });
    requestDraftTranscript.addEventListener('scroll', updateRequestDraftTranscriptPinnedToBottom);
    sendRequestDraftButton.addEventListener('click', () => sendRequestDraftMessage().catch((error) => {
      formError.hidden = false;
      formError.textContent = error.message;
      updateRequestDraftPanel(error.message);
    }));
    attachRequestDraftImageButton.addEventListener('click', () => requestDraftImageInput.click());
    requestDraftImageInput.addEventListener('change', () => {
      attachImagesToRequestDraft(requestDraftImageInput.files).catch((error) => {
        setRequestDraftAttachmentStatusMessage(error.message || translateRequest('draftAttachmentFailed'), 'error');
      });
    });
    requestDraftComposer.addEventListener('dragenter', (event) => {
      if (!Array.from(event.dataTransfer?.types || []).includes('Files')) return;
      requestDraftDropDepth += 1;
      updateRequestDraftDropTarget(true);
    });
    requestDraftComposer.addEventListener('dragover', (event) => {
      if (!Array.from(event.dataTransfer?.types || []).includes('Files')) return;
      event.preventDefault();
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
      updateRequestDraftDropTarget(true);
    });
    requestDraftComposer.addEventListener('dragleave', (event) => {
      if (!Array.from(event.dataTransfer?.types || []).includes('Files')) return;
      requestDraftDropDepth = Math.max(0, requestDraftDropDepth - 1);
      if (requestDraftDropDepth === 0 || event.target === requestDraftComposer) updateRequestDraftDropTarget(false);
    });
    requestDraftComposer.addEventListener('drop', (event) => {
      const files = Array.from(event.dataTransfer?.files || []).filter((file) => file && typeof file.type === 'string' && file.type.startsWith('image/'));
      requestDraftDropDepth = 0;
      updateRequestDraftDropTarget(false);
      if (!files.length) return;
      event.preventDefault();
      attachImagesToRequestDraft(files).catch((error) => {
        setRequestDraftAttachmentStatusMessage(error.message || translateRequest('draftAttachmentFailed'), 'error');
      });
    });
    settingsForm.addEventListener('submit', saveModelSettings);
    refreshModelOptionsButton.addEventListener('click', () => loadModelSettings(true, { preserveState: true }).catch((error) => setSettingsStatus(error.message, 'error')));
    testSlackSettingsButton.addEventListener('click', () => runSlackSettingsTest());
    startSlackReceiveTestButton.addEventListener('click', () => startSlackReceiveTest());
    copySlackReceiveTestButton.addEventListener('click', () => copySlackReceiveTestInstruction());
    clearSlackBotTokenButton.addEventListener('click', () => {
      slackBotTokenInput.value = '';
      slackBotTokenClearRequested = true;
      updateSlackTokenStatus(slackBotTokenStatus, lastSettingsPayload?.slack_bot_token_masked, lastSettingsPayload?.slack_bot_token_configured);
    });
    clearSlackAppTokenButton.addEventListener('click', () => {
      slackAppTokenInput.value = '';
      slackAppTokenClearRequested = true;
      updateSlackTokenStatus(slackAppTokenStatus, lastSettingsPayload?.slack_app_token_masked, lastSettingsPayload?.slack_app_token_configured);
    });
    slackBotTokenInput.addEventListener('input', () => {
      slackBotTokenClearRequested = false;
      updateSlackTokenStatus(slackBotTokenStatus, lastSettingsPayload?.slack_bot_token_masked, lastSettingsPayload?.slack_bot_token_configured);
    });
    slackAppTokenInput.addEventListener('input', () => {
      slackAppTokenClearRequested = false;
      updateSlackTokenStatus(slackAppTokenStatus, lastSettingsPayload?.slack_app_token_masked, lastSettingsPayload?.slack_app_token_configured);
    });
    slackDefaultChannelInput.addEventListener('input', () => {
      updateSlackChannelState();
    });
    function handleAssistantModeVisibilityChange() {
      updateWorkerLiveLogsControlVisibility();
    }

    workerLiveLogsModeInput.addEventListener('change', () => {
      if (workerLiveLogsModeInput.value === 'true') {
        window.alert('이 모드는 더 많은 토큰을 사용합니다.');
      }
    });
    runtimeCodingAssistantInput.addEventListener('input', handleAssistantModeVisibilityChange);
    runtimeCodingAssistantInput.addEventListener('change', () => {
      handleAssistantModeVisibilityChange();
      roleSettingConfigs.forEach((config) => {
        if ((config.backendInput.value || 'default') !== 'default') return;
        resetRoleModelSelection(config);
      });
      renderAllRoleModelOptions();
      loadModelSettings(true, { preserveState: true }).catch((error) => setSettingsStatus(error.message, 'error'));
    });
    roleSettingConfigs.forEach((config) => {
      const { backendInput, modelInput, modelSelectInput, role } = config;
      backendInput.addEventListener('change', () => {
        resetRoleModelSelection(config);
        renderRoleModelOptions(role);
        const selectedBackend = effectiveRoleBackend(role);
        if (selectedBackend) {
          loadModelSettings(true, { preserveState: true, assistantOverride: selectedBackend, updateSummary: false }).catch((error) => setSettingsStatus(error.message, 'error'));
        }
      });
      modelSelectInput.addEventListener('change', () => {
        applyRoleModelSelection(config, modelSelectInput.value);
        if (modelSelectInput.value === customModelOptionValue) {
          modelInput.focus();
        }
      });
    });
    board.addEventListener('click', (event) => {
      const retrospectiveButton = event.target.closest('.target-branch-retrospective');
      if (retrospectiveButton) {
        openRetrospectiveModal(retrospectiveButton.dataset.targetRepo || '', retrospectiveButton.dataset.baseBranch || '').catch((error) => {
          retrospectiveStatus.dataset.tone = 'error';
          retrospectiveStatus.textContent = error.message;
          setRetrospectiveModalOpen(true);
        });
        return;
      }
      const branchLabel = event.target.closest('.target-branch-label');
      if (branchLabel) {
        toggleFinalBranchGroup(branchLabel);
        return;
      }
      const button = event.target.closest('[data-task-id]');
      if (!button) return;
      loadTaskDetail(button.dataset.taskId, false, { snapshot: boardTaskSnapshots.get(button.dataset.taskId) || null });
    });
    board.addEventListener('keydown', (event) => {
      const branchLabel = event.target.closest('.target-branch-label');
      if (!branchLabel) return;
      if (event.key !== 'Enter' && event.key !== ' ') return;
      if (event.target.closest('.target-branch-retrospective')) return;
      event.preventDefault();
      toggleFinalBranchGroup(branchLabel);
    });
    boardPhaseTabs.addEventListener('click', (event) => {
      const button = event.target.closest('[data-board-phase]');
      if (!button) return;
      boardPhaseManuallySelected = true;
      activeBoardPhase = button.dataset.boardPhase;
      renderBoardPhaseTabs();
      loadBoard();
    });
    taskTabOverview.addEventListener('click', () => setTaskTab('overview'));
    taskTabLogs.addEventListener('click', () => setTaskTab('logs'));
    taskTabChangedFiles.addEventListener('click', () => setTaskTab('changed-files'));
    taskTabQaChecklist.addEventListener('click', () => setTaskTab('qa-checklist'));
    taskTabReviewerQa.addEventListener('click', () => setTaskTab('reviewer-qa'));
    taskTabReviewNote.addEventListener('click', () => setTaskTab('review-note'));
    taskApprovalGateNotice.addEventListener('click', (event) => {
      const actionButton = event.target.closest('[data-approval-gate-action]');
      if (!actionButton) return;
      const action = actionButton.dataset.approvalGateAction;
      if (action === 'qa-checklist') setTaskTab('qa-checklist');
    });
    taskTabEditor.addEventListener('click', () => setTaskTab('editor'));
    requestComposerTabFields.addEventListener('click', () => setRequestComposerTab('fields'));
    requestComposerTabAssistant.addEventListener('click', () => setRequestComposerTab('assistant'));
    requestComposerTabs.addEventListener('keydown', (event) => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      if (event.key === 'Home') {
        setRequestComposerTab('assistant');
        requestComposerTabAssistant.focus();
        return;
      }
      if (event.key === 'End') {
        setRequestComposerTab('fields');
        requestComposerTabFields.focus();
        return;
      }
      const nextTab = activeRequestComposerTab === 'fields' ? 'assistant' : 'fields';
      setRequestComposerTab(nextTab);
      (nextTab === 'fields' ? requestComposerTabFields : requestComposerTabAssistant).focus();
    });
    taskLogFiles.addEventListener('click', (event) => {
      const button = event.target.closest('[data-log-name]');
      if (!button || !activeTaskLogs) return;
      activeLogName = button.dataset.logName;
      renderTaskLogs(activeTaskLogs);
      scrollTaskLogViewerToBottom();
    });
    taskLogViewer.addEventListener('scroll', updateTaskLogViewerPinnedToBottom);
    taskReviewerQaTranscript.addEventListener('scroll', updateReviewerQaTranscriptPinnedToBottom);
    taskReviewerQaTranscript.addEventListener('click', (event) => {
      if (!event.target.closest('[data-reviewer-qa-rerequest]')) return;
      rerequestReviewerQa().catch((error) => {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        updateReviewerQaPanel();
      });
    });
    taskChangedFiles.addEventListener('click', (event) => { const button = event.target.closest('[data-changed-file-id]'); if (!button) return; loadChangedFile(activeTaskId, button.dataset.changedFileId); });
    async function handleChangedFileViewedToggleChange(event) {
      const toggle = event.target.closest('[data-viewed-changed-file-id]');
      if (!toggle || !activeTaskId) return;
      const changedFileId = toggle.dataset.viewedChangedFileId;
      const viewed = Boolean(toggle.checked);
      if (!changedFileId) return;
      try {
        await setChangedFileViewed(activeTaskId, changedFileId, viewed);
        if (viewed) {
          const nextFileId = nextUnviewedChangedFileId(changedFileId);
          if (nextFileId && nextFileId !== activeChangedFileId) {
            loadChangedFile(activeTaskId, nextFileId);
          }
        }
      } catch (_error) {
        toggle.checked = !toggle.checked;
      }
    }
    taskChangedFiles.addEventListener('change', handleChangedFileViewedToggleChange);
    taskChangedFileSummary.addEventListener('change', handleChangedFileViewedToggleChange);
    taskQaChecklistItems.addEventListener('change', (event) => {
      const checkToggle = event.target.closest('[data-qa-check]');
      const skipToggle = event.target.closest('[data-qa-skip]');
      const noteInput = event.target.closest('[data-qa-note]');
      const itemId = checkToggle?.dataset.qaCheck || skipToggle?.dataset.qaSkip || noteInput?.dataset.qaNote || '';
      if (!activeTaskId || !itemId) return;
      const patch = checkToggle
        ? { checked: Boolean(checkToggle.checked), skipped: false }
        : skipToggle
          ? { skipped: Boolean(skipToggle.checked), checked: false }
          : { note: noteInput.value || '' };
      setQaChecklistItemState(activeTaskId, itemId, patch).catch((error) => {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        renderQaChecklistPanel();
      });
    });
    taskDiffShell.addEventListener('click', (event) => {
      const lineCommentButton = event.target.closest('[data-line-comment-action]');
      if (lineCommentButton && activeChangedFileDetail) {
        const anchor = (activeChangedFileDetail.comments || []).map((comment) => comment.anchor || {}).concat(activeChangedFileDetail.hunks.flatMap((hunk) => hunk.rows.flatMap((row) => [buildLineAnchor(activeChangedFileDetail, hunk, row.left, 'left'), buildLineAnchor(activeChangedFileDetail, hunk, row.right, 'right')].filter(Boolean)))).find((item) => buildLineAnchorKey(item) === lineCommentButton.dataset.lineAnchorKey);
        if (anchor) openInlineCommentComposer(anchor);
        return;
      }
      if (event.target.closest('[data-inline-comment-cancel]')) {
        closeInlineCommentComposer();
        return;
      }
      if (event.target.closest('[data-inline-comment-submit]')) {
        submitInlineComment();
        return;
      }
      const deleteCommentButton = event.target.closest('[data-delete-comment-id]');
      if (deleteCommentButton) {
        deleteInlineComment(deleteCommentButton.dataset.deleteCommentId);
      }
    });
    taskDiffShell.addEventListener('input', (event) => {
      if (!event.target.matches('[data-inline-comment-fallback]')) return;
      activeInlineCommentDraft = event.target.value;
      updateInlineCommentComposerState();
    });
    taskHumanReviewEditorFallback.addEventListener('input', updateHumanReviewPanel);
    taskChangedFilesSplitter.addEventListener('pointerdown', (event) => {
      if (!isDesktopDiffLayout() || event.button !== 0) return;
      taskChangedFilesLayout.classList.add('is-resizing');
      taskChangedFilesSplitter.setPointerCapture(event.pointerId);
      updateTaskChangedFilesPaneWidthFromClientX(event.clientX, { persist: true });
      event.preventDefault();
    });
    taskChangedFilesSplitter.addEventListener('pointermove', (event) => {
      if (!taskChangedFilesSplitter.hasPointerCapture(event.pointerId)) return;
      updateTaskChangedFilesPaneWidthFromClientX(event.clientX, { persist: true });
    });
    taskChangedFilesSplitter.addEventListener('pointerup', (event) => {
      if (!taskChangedFilesSplitter.hasPointerCapture(event.pointerId)) return;
      taskChangedFilesSplitter.releasePointerCapture(event.pointerId);
      taskChangedFilesLayout.classList.remove('is-resizing');
      updateTaskChangedFilesPaneWidthFromClientX(event.clientX, { persist: true });
    });
    taskChangedFilesSplitter.addEventListener('pointercancel', (event) => {
      if (!taskChangedFilesSplitter.hasPointerCapture(event.pointerId)) return;
      taskChangedFilesSplitter.releasePointerCapture(event.pointerId);
      taskChangedFilesLayout.classList.remove('is-resizing');
      syncTaskChangedFilesPaneWidth();
    });
    taskChangedFilesSplitter.addEventListener('keydown', (event) => {
      if (!isDesktopDiffLayout()) return;
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
      const direction = event.key === 'ArrowLeft' ? -24 : 24;
      applyTaskChangedFilesPaneWidth(readTaskChangedFilesPaneWidth() + direction, { persist: true });
      event.preventDefault();
    });
    window.addEventListener('resize', () => {
      syncTaskChangedFilesPaneWidth();
      if (activeTaskTab === 'review-note') syncHumanReviewEditorHeight();
      if (activeTaskTab === 'editor' && planEditMode && activeArtifactName === 'PLAN.md') schedulePlanEditorHeightSync();
    });
    saveHumanReviewNoteButton.addEventListener('click', () => saveHumanReviewNoteIfNeeded().catch((error) => { taskModalError.hidden = false; taskModalError.textContent = error.message; updateHumanReviewPanel(); }));
    askReviewerQuestionButton.addEventListener('click', () => askReviewerQuestion().catch((error) => { taskModalError.hidden = false; taskModalError.textContent = error.message; updateReviewerQaPanel(); }));
    taskReviewerQaInput.addEventListener('input', () => updateReviewerQaPanel());
    requestChangesButton.addEventListener('click', rejectVerification);
    approveHumanReviewButton.addEventListener('click', openApprovalChoiceModal);
    approvalChoiceTargetButton.addEventListener('click', () => { approveVerification('target-branch'); });
    approvalChoiceNewBranchButton.addEventListener('click', () => { approveVerification('new-branch'); });
    taskMarkdownFiles.addEventListener('click', (event) => { const button = event.target.closest('[data-artifact-file]'); if (!button || !activeTaskDetail) return; const file = button.dataset.artifactFile; if (!file) return; planEditMode = false; loadMarkdownArtifact(activeTaskId, file); });
    taskArtifactSubtabs.addEventListener('click', (event) => { const button = event.target.closest('[data-artifact-file]'); if (!button || !activeTaskDetail) return; const file = button.dataset.artifactFile; if (!file) return; planEditMode = false; loadMarkdownArtifact(activeTaskId, file); });
    taskOverview.addEventListener('input', (event) => {
      if (event.target.id === 'completed-group-input') updateCompletedGroupControls();
    });
    taskOverview.addEventListener('click', (event) => {
      const button = event.target.closest('[data-action]');
      if (!button) return;
      const action = button.dataset.action;
      if (action === 'save-completed-group') {
        const nextGroup = (completedGroupInput()?.value || '').trim() || null;
        saveCompletedGroupOverride(nextGroup).catch((error) => {
          taskModalError.hidden = false;
          taskModalError.textContent = error.message;
          updateCompletedGroupControls();
        });
      }
      if (action === 'clear-completed-group') {
        const input = completedGroupInput();
        if (input) input.value = '';
        saveCompletedGroupOverride(null).catch((error) => {
          taskModalError.hidden = false;
          taskModalError.textContent = error.message;
          updateCompletedGroupControls();
        });
      }
    });
    togglePlanEditButton.addEventListener('click', togglePlanEditMode);
    savePlanButton.addEventListener('click', savePlanArtifact);
    approvePlanButton.addEventListener('click', approvePlan);
    startVerificationButton.addEventListener('click', startVerification);
    retryVerificationApplyButton.addEventListener('click', retryVerificationApply);
    resumePlannerButton.addEventListener('click', openResumePlannerChoiceModal);
    resumePlannerChoiceButton.addEventListener('click', resumePlanner);
    resumeImplementerButton.addEventListener('click', openResumeImplementerChoiceModal);
    resumeImplementerChoicePinnedButton.addEventListener('click', () => { resumeImplementer('pinned'); });
    resumeImplementerChoiceCurrentButton.addEventListener('click', () => { resumeImplementer('current-settings'); });
    resumeReviewerButton.addEventListener('click', openResumeReviewerChoiceModal);
    resumeReviewerChoicePinnedButton.addEventListener('click', () => { resumeReviewer('pinned'); });
    resumeReviewerChoiceCurrentButton.addEventListener('click', () => { resumeReviewer('current-settings'); });
    resumeReviewLoopButton.addEventListener('click', resumeReviewLoop);
    deleteTaskButton.addEventListener('click', deleteTask);
    ['title', 'target_repo', 'base_branch'].forEach((name) => { requestForm.elements[name].addEventListener('blur', validateForm); });
    targetRepoInput.addEventListener('input', () => { targetRepoInput.dataset.autofilled = 'false'; });
    targetRepoInput.addEventListener('input', applyRepoDefaults);
    targetRepoInput.addEventListener('input', queueTargetRepoBranchLookup);
    targetRepoInput.addEventListener('change', applyRepoDefaults);
    targetRepoInput.addEventListener('change', loadTargetRepoBranches);
    targetRepoInput.addEventListener('blur', loadTargetRepoBranches);
    runtimeThemeInput.addEventListener('change', () => applyRuntimeTheme(runtimeThemeInput.value));
    baseBranchInput.addEventListener('input', () => { baseBranchInput.dataset.autofilled = 'false'; });
    requestGoalEditorFallback.addEventListener('input', () => {
      syncRequestGoalField(requestGoalEditorFallback.value);
      clearRequestFieldError('goal');
    });
    requestGoalEditorFallback.addEventListener('blur', validateForm);
    scopeField.addEventListener('input', () => { scopeField.dataset.autofilled = 'false'; });
    outOfScopeField.addEventListener('input', () => { outOfScopeField.dataset.autofilled = 'false'; });
    resetFormState();
    applyRuntimeTheme(initialRuntimeTheme);
    void loadModelSettings(false, { allowHidden: true }).catch(() => {});
    loadTargetRepoOptions();
