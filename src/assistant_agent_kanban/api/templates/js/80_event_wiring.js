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
    if (authUserLabel) authUserLabel.addEventListener('click', () => openAccountModal());
    if (closeAccountModalButton) closeAccountModalButton.addEventListener('click', () => setAccountModalOpen(false));
    if (accountModal) {
      accountModal.addEventListener('click', (event) => {
        if (event.target === accountModal) {
          setAccountModalOpen(false);
        }
      });
    }
    if (logoutButton) logoutButton.addEventListener('click', () => logout().catch(() => { window.location.href = '/login'; }));
    if (createUserButton) createUserButton.addEventListener('click', () => createUser());
    if (changePasswordButton) changePasswordButton.addEventListener('click', () => changeOwnPassword());
    [newUserUsernameInput, newUserPasswordInput].forEach((input) => {
      if (!input) return;
      input.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        createUser();
      });
    });
    [currentUserPasswordInput, newUserPasswordChangeInput, confirmUserPasswordChangeInput].forEach((input) => {
      if (!input) return;
      input.addEventListener('keydown', (event) => {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        changeOwnPassword();
      });
    });
    document.querySelectorAll('.password-visibility-toggle').forEach((button) => {
      button.addEventListener('click', () => {
        const targetId = button.dataset.target;
        const input = document.getElementById(targetId);
        if (!input) return;
        const isPassword = input.type === 'password';
        input.type = isPassword ? 'text' : 'password';
        
        const eyeOpen = button.querySelector('.eye-open-icon');
        const eyeClosed = button.querySelector('.eye-closed-icon');
        if (eyeOpen && eyeClosed) {
          eyeOpen.hidden = !isPassword;
          eyeClosed.hidden = isPassword;
        }
      });
    });
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
    document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && !modal.hidden) { clearMessages(); void syncRequestComposerDraftState({ immediate: true, silent: true }); setModalOpen(false); } if (event.key === 'Escape' && !settingsModal.hidden) closeSettingsModal({ restore: true }); if (event.key === 'Escape' && accountModal && !accountModal.hidden) setAccountModalOpen(false); if (event.key === 'Escape' && !approvalChoiceModal.hidden) { if (approvalSubmissionInFlight) return; setApprovalChoiceModalOpen(false); } else if (event.key === 'Escape' && !resumePlannerChoiceModal.hidden) { if (resumePlannerSubmissionInFlight) return; setResumePlannerChoiceModalOpen(false); } else if (event.key === 'Escape' && !resumeImplementerChoiceModal.hidden) { if (resumeImplementerSubmissionInFlight) return; setResumeImplementerChoiceModalOpen(false); } else if (event.key === 'Escape' && !resumeReviewerChoiceModal.hidden) { if (resumeReviewerSubmissionInFlight) return; setResumeReviewerChoiceModalOpen(false); } else if (event.key === 'Escape' && !taskModal.hidden) { setTaskModalOpen(false); } if (event.key === 'Escape' && !retrospectiveModal.hidden) { setRetrospectiveModalOpen(false); } if (event.key === 'Escape' && directoryPickerModal && !directoryPickerModal.hidden) { setDirectoryPickerModalOpen(false); } });
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
    refreshModelOptionsButton.addEventListener('click', () => loadModelSettings(true, { preserveState: true }).catch((error) => setSettingsStatus(error.message, 'error', { scope: 'roles' })));
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
    gitTokenInput?.addEventListener('input', () => {
      updateGitTokenStatus(lastSettingsPayload);
    });
    gitTokenUnlockKeyInput?.addEventListener('input', () => {
      updateGitUnlockKeyStatus();
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
      loadModelSettings(true, { preserveState: true }).catch((error) => setSettingsStatus(error.message, 'error', { scope: 'roles' }));
    });
    roleSettingConfigs.forEach((config) => {
      const { backendInput, modelInput, modelSelectInput, role } = config;
      backendInput.addEventListener('change', () => {
        resetRoleModelSelection(config);
        renderRoleModelOptions(role);
        const selectedBackend = effectiveRoleBackend(role);
        if (selectedBackend) {
          loadModelSettings(true, { preserveState: true, assistantOverride: selectedBackend, updateSummary: false }).catch((error) => setSettingsStatus(error.message, 'error', { scope: 'roles' }));
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
      const newRequestBtn = event.target.closest('.final-project-new-request');
      if (newRequestBtn) {
        openComposerWithRepo(newRequestBtn.dataset.projectPath || '').catch(console.error);
        return;
      }
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
    taskTabInspector.addEventListener('click', () => setTaskTab('inspector'));
    taskTabLogs.addEventListener('click', () => setTaskTab('logs'));
    taskTabChangedFiles.addEventListener('click', () => setTaskTab('changed-files'));
    taskTabQaChecklist.addEventListener('click', () => setTaskTab('qa-checklist'));
    taskTabReviewerQa.addEventListener('click', () => setTaskTab('reviewer-qa'));
    taskTabReviewNote.addEventListener('click', () => setTaskTab('review-note'));
    taskApprovalGateNotice.addEventListener('click', (event) => {
      const copyButton = event.target.closest('[data-copy-value]');
      if (copyButton) {
        copyTextToClipboard(copyButton.dataset.copyValue || '', copyButton);
        return;
      }
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
    taskQaChecklistItems.addEventListener('pointerdown', (event) => {
      if (!event.target.closest('[data-qa-check], [data-qa-skip], [data-qa-note]')) return;
      rememberQaChecklistScrollState();
    });
    taskQaChecklistItems.addEventListener('keydown', (event) => {
      if (event.key !== ' ' && event.key !== 'Enter') return;
      if (!event.target.closest('[data-qa-check], [data-qa-skip], [data-qa-note]')) return;
      rememberQaChecklistScrollState();
    });
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
      const scrollState = consumeQaChecklistScrollState();
      setQaChecklistItemState(activeTaskId, itemId, patch, { scrollState }).catch((error) => {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        renderQaChecklistPanel({ scrollState });
        scheduleQaChecklistScrollRestore(scrollState);
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
    refreshTaskInspectionButton.addEventListener('click', () => loadTaskInspection(activeTaskId, { force: true }).catch((error) => { taskModalError.hidden = false; taskModalError.textContent = error.message; }));
    askTaskInspectorButton.addEventListener('click', () => askTaskInspector().catch((error) => { taskModalError.hidden = false; taskModalError.textContent = error.message; updateTaskInspectorPanel(); }));
    taskInspectorInput.addEventListener('input', () => updateTaskInspectorPanel());
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
      if (action === 'open-inspector') {
        setTaskTab('inspector');
      }
    });
    togglePlanEditButton.addEventListener('click', togglePlanEditMode);
    savePlanButton.addEventListener('click', savePlanArtifact);
    approvePlanButton.addEventListener('click', approvePlan);
    splitPlanButton.addEventListener('click', splitPlan);
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
    rerequestTaskButton.addEventListener('click', rerequestTask);
    cancelTaskButton.addEventListener('click', cancelTask);
    deleteTaskButton.addEventListener('click', deleteTask);
    ['title', 'target_repo', 'base_branch'].forEach((name) => { requestForm.elements[name].addEventListener('blur', validateForm); });
    targetRepoInput.addEventListener('input', () => {
      targetRepoInput.dataset.autofilled = 'false';
      if (!normalizeRepoPath(targetRepoInput.value)) return;
      targetRepoInput.closest('.field')?.classList.remove('field-attention');
      clearRequestFieldError('target_repo');
      if (formError.textContent === translateRequest('draftTargetRepoRequired')) {
        formError.hidden = true;
        formError.textContent = '';
      }
    });
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
    if (btnBrowseRepoRoot) {
      btnBrowseRepoRoot.addEventListener('click', () => {
        if (isRepoDiscoveryReadonly()) return;
        openDirectoryPicker('repo_discovery_root');
      });
    }
    if (btnBrowseTargetRepo) {
      btnBrowseTargetRepo.addEventListener('click', () => openDirectoryPicker('target_repo'));
    }
    if (btnDirectoryPickerClose) {
      btnDirectoryPickerClose.addEventListener('click', () => setDirectoryPickerModalOpen(false));
    }
    if (btnDirectoryPickerSelect) {
      btnDirectoryPickerSelect.addEventListener('click', selectDirectoryPickerCurrent);
    }
    if (directoryPickerList) {
      directoryPickerList.addEventListener('click', (event) => {
        const item = event.target.closest('.directory-picker-item');
        if (item && item.dataset.path) {
          loadPickerDirectory(item.dataset.path).catch((err) => {
            console.error(err);
          });
        }
      });
    }
    resetFormState();
    applyRuntimeTheme(initialRuntimeTheme);
    void loadAuthState().catch(() => {});
    void loadModelSettings(false, { allowHidden: true }).catch(() => {});
    loadTargetRepoOptions();
