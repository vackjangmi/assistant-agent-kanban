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
        const requestBody = gitUnlockBodyForOperation({ note: getHumanReviewEditorContent() });
        if (requestBody === null) return;
        const response = await fetch(`/api/tasks/${activeTaskId}/reject-verification`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(requestBody),
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
      if (activeTaskDetail?.metadata?.integration?.remote_review_branch) {
        approveVerification('new-branch');
        return;
      }
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
        const requestBody = gitUnlockBodyForOperation({ completion_mode: completionMode || 'new-branch' });
        if (requestBody === null) return;
        const response = await fetch(`/api/tasks/${activeTaskId}/approve-verification`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(requestBody),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateTask('failedApproveVerification'));
        approvalSubmissionInFlight = false;
        await loadBoard();
        const remoteUrl = payload?.integration?.remote_merge_request_url || '';
        if (remoteUrl) {
          setApprovalChoiceModalOpen(false, { force: true });
          await loadTaskDetail(activeTaskId, true, { softRefresh: true, reloadArtifact: false });
          setTaskTab('overview');
          return;
        }
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
