
    targetRepoInput.value = defaultTargetRepo;
    runtimeLanguageInput.value = initialRuntimeLanguage;
    baseBranchInput.value = defaultBaseBranch;
    baseBranchInput.dataset.autofilled = 'true';
    lastAutoBaseBranch = defaultBaseBranch;
    applyRuntimeSettingsTranslations();
    applyRequestTranslations();
    applyHumanReviewTranslations();
    applyTaskTranslations();

    function currentUiLanguage() {
      return settingsTranslations[runtimeLanguageInput.value] ? runtimeLanguageInput.value : initialRuntimeLanguage;
    }

    function translateSettings(key, variables = {}) {
      const language = currentUiLanguage();
      const table = settingsTranslations[language] || settingsTranslations.EN;
      const template = table[key] || settingsTranslations.EN[key] || '';
      return Object.entries(variables).reduce((result, [name, value]) => result.replaceAll(`{${name}}`, String(value)), template);
    }

    function translateRequest(key, variables = {}) {
      const language = currentUiLanguage();
      const table = requestTranslations[language] || requestTranslations.EN;
      const template = table[key] || requestTranslations.EN[key] || '';
      return Object.entries(variables).reduce((result, [name, value]) => result.replaceAll(`{${name}}`, String(value)), template);
    }

    function translateHumanReview(key, variables = {}) {
      const language = currentUiLanguage();
      const table = humanReviewTranslations[language] || humanReviewTranslations.EN;
      const template = table[key] || humanReviewTranslations.EN[key] || '';
      return Object.entries(variables).reduce((result, [name, value]) => result.replaceAll(`{${name}}`, String(value)), template);
    }

    function translateTask(key, variables = {}) {
      const language = currentUiLanguage();
      const table = taskTranslations[language] || taskTranslations.EN;
      const template = table[key] || taskTranslations.EN[key] || '';
      return Object.entries(variables).reduce((result, [name, value]) => result.replaceAll(`{${name}}`, String(value)), template);
    }

    function formatSettingsApiError(detail) {
      if (!detail) return translateSettings('settingsSaveFailed');
      if (typeof detail === 'string') return detail;
      if (detail.code === 'settings.model_not_discovered') {
        return translateSettings('errorModelNotDiscovered', { field: detail.field || 'model' });
      }
      if (detail.code === 'settings.backend_unavailable') {
        return translateSettings('errorBackendUnavailable', { field: detail.field || 'assistant', message: detail.message || 'not installed' });
      }
      return translateSettings('settingsSaveFailed');
    }

    function setRequestText(id, key) {
      const node = document.getElementById(id);
      if (!node) return;
      node.textContent = translateRequest(key);
    }

    function setRequestHtml(id, key, variables = {}) {
      const node = document.getElementById(id);
      if (!node) return;
      node.innerHTML = translateRequest(key, variables);
    }

    function setSettingsText(id, key) {
      const node = document.getElementById(id);
      if (!node) return;
      node.textContent = translateSettings(key);
    }

    function setSettingsHtml(id, key, variables = {}) {
      const node = document.getElementById(id);
      if (!node) return;
      node.innerHTML = translateSettings(key, variables);
    }

    function setTaskText(id, key, variables = {}) {
      const node = document.getElementById(id);
      if (!node) return;
      node.textContent = translateTask(key, variables);
    }

    function applyTaskTranslations() {
      setTaskText('task-modal-title', 'modalTitle');
      document.getElementById('task-modal-subtitle').innerHTML = renderTaskSubtitleTags();
      setTaskText('retrospective-modal-title', 'retrospectiveModalTitle');
      retrospectiveViewTitle.textContent = activeRetrospectiveRecord?.exists ? translateTask('retrospectiveViewTitle') : '';
      setTaskText('retrospective-action-title', 'retrospectiveActionTitle');
      retrospectiveCompareLabel.textContent = translateTask('retrospectiveCompareLabel');
      retrospectiveCompareBranchInput.placeholder = translateTask('retrospectiveComparePlaceholder');
      retrospectiveCompareHelp.textContent = translateTask('retrospectiveCompareHelp');
      startVerificationButton.textContent = translateTask('startVerification');
      retryVerificationApplyButton.textContent = translateTask('retryVerificationApply');
      resumePlannerButton.textContent = translateTask('resumePlanner');
      resumeImplementerButton.textContent = translateTask('resumeImplementer');
      resumeReviewerButton.textContent = translateTask('resumeReviewer');
      document.getElementById('resume-planner-choice-title').textContent = translateTask('resumePlannerChoiceTitle');
      document.getElementById('resume-planner-choice-description').textContent = translateTask('resumePlannerChoiceDescription');
      document.getElementById('resume-planner-choice-copy-title').textContent = translateTask('resumePlannerChoiceCopyTitle');
      document.getElementById('resume-planner-choice-copy-body').textContent = translateTask('resumePlannerChoiceCopyBody');
      document.getElementById('resume-planner-message-label').textContent = translateTask('resumePlannerMessageLabel');
      document.getElementById('resume-planner-message-help').textContent = translateTask('resumePlannerMessageHelp');
      resumePlannerMessageInput.placeholder = translateTask('resumePlannerMessagePlaceholder');
      document.getElementById('resume-planner-choice-action-title').textContent = translateTask('resumePlannerChoiceActionTitle');
      document.getElementById('resume-planner-choice-action-description').textContent = translateTask('resumePlannerChoiceActionDescription');
      resumePlannerChoiceButton.textContent = translateTask('resumePlannerChoiceAction');
      closeResumePlannerChoiceButton.textContent = translateTask('resumePlannerChoiceClose');
      document.getElementById('resume-implementer-choice-title').textContent = translateTask('resumeImplementerChoiceTitle');
      document.getElementById('resume-implementer-choice-description').textContent = translateTask('resumeImplementerChoiceDescription');
      document.getElementById('resume-implementer-choice-copy-title').textContent = translateTask('resumeImplementerChoiceCopyTitle');
      document.getElementById('resume-implementer-choice-copy-body').textContent = translateTask('resumeImplementerChoiceCopyBody');
      document.getElementById('resume-implementer-message-label').textContent = translateTask('resumeWorkerMessageLabel');
      document.getElementById('resume-implementer-message-help').textContent = translateTask('resumeWorkerMessageHelp');
      resumeImplementerMessageInput.placeholder = translateTask('resumeImplementerMessagePlaceholder');
      document.getElementById('resume-implementer-choice-pinned-title').textContent = translateTask('resumeImplementerChoicePinnedTitle');
      document.getElementById('resume-implementer-choice-pinned-description').textContent = translateTask('resumeImplementerChoicePinnedDescription');
      document.getElementById('resume-implementer-choice-current-title').textContent = translateTask('resumeImplementerChoiceCurrentTitle');
      document.getElementById('resume-implementer-choice-current-description').textContent = translateTask('resumeImplementerChoiceCurrentDescription');
      resumeImplementerChoicePinnedButton.textContent = translateTask('resumeImplementerChoicePinnedAction');
      resumeImplementerChoiceCurrentButton.textContent = translateTask('resumeImplementerChoiceCurrentAction');
      closeResumeImplementerChoiceButton.textContent = translateTask('resumeImplementerChoiceClose');
      document.getElementById('resume-reviewer-choice-title').textContent = translateTask('resumeReviewerChoiceTitle');
      document.getElementById('resume-reviewer-choice-description').textContent = translateTask('resumeReviewerChoiceDescription');
      document.getElementById('resume-reviewer-choice-copy-title').textContent = translateTask('resumeReviewerChoiceCopyTitle');
      document.getElementById('resume-reviewer-choice-copy-body').textContent = translateTask('resumeReviewerChoiceCopyBody');
      document.getElementById('resume-reviewer-message-label').textContent = translateTask('resumeWorkerMessageLabel');
      document.getElementById('resume-reviewer-message-help').textContent = translateTask('resumeWorkerMessageHelp');
      resumeReviewerMessageInput.placeholder = translateTask('resumeReviewerMessagePlaceholder');
      document.getElementById('resume-reviewer-choice-pinned-title').textContent = translateTask('resumeReviewerChoicePinnedTitle');
      document.getElementById('resume-reviewer-choice-pinned-description').textContent = translateTask('resumeReviewerChoicePinnedDescription');
      document.getElementById('resume-reviewer-choice-current-title').textContent = translateTask('resumeReviewerChoiceCurrentTitle');
      document.getElementById('resume-reviewer-choice-current-description').textContent = translateTask('resumeReviewerChoiceCurrentDescription');
      resumeReviewerChoicePinnedButton.textContent = translateTask('resumeReviewerChoicePinnedAction');
      resumeReviewerChoiceCurrentButton.textContent = translateTask('resumeReviewerChoiceCurrentAction');
      closeResumeReviewerChoiceButton.textContent = translateTask('resumeReviewerChoiceClose');
      resumeReviewLoopButton.textContent = translateTask('resumeReviewLoop');
      approvePlanButton.textContent = translateTask('approvePlan');
      splitPlanButton.textContent = translateTask('splitPlan');
      rerequestTaskButton.textContent = translateTask('rerequestTask');
      cancelTaskButton.textContent = translateTask('cancelTask');
      deleteTaskButton.textContent = translateTask('deleteTask');
      closeTaskModalButton.textContent = translateTask('close');
      closeTaskModalButton.setAttribute('aria-label', translateTask('closeAria'));
      closeRetrospectiveModalButton.textContent = translateTask('retrospectiveClose');
      retrospectiveCreateTargetButton.textContent = translateTask('retrospectiveCreateTarget');
      retrospectiveCreateBranchButton.textContent = translateTask('retrospectiveCreateBranch');
      taskTabOverview.textContent = translateTask('tabOverview');
      taskTabLogs.textContent = translateTask('tabLogs');
      taskTabEditor.textContent = translateTask('tabViewer');
      taskTabChangedFiles.textContent = translateTask('tabChangedFiles');
      taskTabQaChecklist.textContent = translateHumanReview('qaChecklistTab');
      taskQaChecklistTitle.textContent = translateHumanReview('qaChecklistTitle');
      taskTabReviewerQa.textContent = translateTask('tabReviewerQa');
      askReviewerQuestionButton.textContent = translateTask('reviewerQaSend');
      taskLogModeBadge.textContent = translateTask('runtimeLogs');
      document.getElementById('task-human-review-note-title').textContent = translateTask('reviewNoteTitle');
      document.querySelector('#task-changed-file-summary strong').textContent = translateTask('changedFilesHeading');
      document.querySelector('#task-changed-file-summary .diff-badge').textContent = translateTask('readOnlyPatch');
      if (!activeTaskDetail) {
        taskOverview.innerHTML = `<div class="muted">${escapeHtml(translateTask('selectTask'))}</div>`;
      }
      if (!activeTaskLogs) {
        taskLogName.textContent = translateTask('noLogSelected');
        taskLogStatus.textContent = translateTask('selectRuntimeLog');
        taskLogViewer.textContent = translateTask('runtimeLogSummaryEmpty');
      } else {
        renderTaskLogs(activeTaskLogs);
      }
      if (!activeRetrospectiveRecord) {
        retrospectiveStatus.textContent = translateTask('retrospectiveIdle');
        retrospectiveContent.textContent = translateTask('retrospectiveNoContent');
        retrospectiveContextRow.innerHTML = renderRetrospectiveContextTags();
      } else {
        setRetrospectiveMode(activeRetrospectiveRecord.exists ? 'view' : 'choice', activeRetrospectiveRecord);
      }
    }

    function applyHumanReviewTranslations() {
      taskTabReviewNote.textContent = translateHumanReview('tab');
      document.getElementById('task-human-review-note-title').textContent = translateHumanReview('noteTitle');
      saveHumanReviewNoteButton.textContent = translateHumanReview('saveNote');
      requestChangesButton.textContent = translateHumanReview('requestChanges');
      approveHumanReviewButton.textContent = translateHumanReview('approve');
      document.getElementById('approval-choice-title').textContent = translateHumanReview('approvalChoiceTitle');
      document.getElementById('approval-choice-description').textContent = translateHumanReview('approvalChoiceDescription');
      document.getElementById('approval-choice-copy-title').textContent = translateHumanReview('approvalChoiceCopyTitle');
      document.getElementById('approval-choice-copy-body').textContent = translateHumanReview('approvalChoiceCopyBody');
      document.getElementById('approval-choice-target-title').textContent = translateHumanReview('approvalChoiceTargetTitle');
      document.getElementById('approval-choice-target-description').textContent = translateHumanReview('approvalChoiceTargetDescription');
      document.getElementById('approval-choice-new-branch-title').textContent = translateHumanReview('approvalChoiceNewBranchTitle');
      document.getElementById('approval-choice-new-branch-description').textContent = translateHumanReview('approvalChoiceNewBranchDescription');
      approvalChoiceTargetButton.textContent = translateHumanReview('approvalChoiceTargetAction');
      approvalChoiceNewBranchButton.textContent = translateHumanReview('approvalChoiceNewBranchAction');
      closeApprovalChoiceButton.textContent = translateHumanReview('approvalChoiceClose');
      if (!activeTaskDetail || activeTaskDetail?.metadata?.state !== 'human-verifying') {
        taskHumanReviewApprovalStatus.textContent = translateHumanReview('approvalReady');
        taskHumanReviewApprovalStatus.dataset.tone = 'neutral';
      } else {
        updateHumanReviewPanel();
      }
    }

    function applyRuntimeSettingsTranslations() {
      openSettingsButton.textContent = translateSettings('openSettings');
      const openOnboardingButton = document.getElementById('open-onboarding');
      if (openOnboardingButton) {
        openOnboardingButton.textContent = translateSettings('openOnboarding');
      }
      setSettingsText('settings-modal-title', 'settingsTitle');
      setSettingsText('settings-modal-description', 'settingsDescription');
      setSettingsText('settings-tab-general', 'settingsTabGeneral');
      setSettingsText('settings-tab-slack', 'settingsTabSlack');
      setSettingsText('settings-tab-roles', 'settingsTabRoles');
      cancelSettingsButton.textContent = translateSettings('closeSettings');
      setSettingsText('settings-copy-title', 'settingsCopyTitle');
      setSettingsHtml('settings-copy-body', 'settingsCopyBody');
      refreshModelOptionsButton.textContent = translateSettings('refreshModels');
      setSettingsText('settings-coding-assistant-label', 'codingAssistantLabel');
      workerLiveLogsModeInput.querySelector('option[value="false"]').textContent = translateSettings('liveLogsDefault');
      workerLiveLogsModeInput.querySelector('option[value="true"]').textContent = translateSettings('liveLogsThinking');
      setSettingsText('settings-basics-heading', 'basicsHeading');
      setSettingsText('settings-basics-description', 'basicsDescription');
      setSettingsText('settings-agents-heading', 'agentsHeading');
      setSettingsText('settings-agents-description', 'agentsDescription');
      setSettingsText('settings-language-title', 'languageTitle');
      setSettingsText('settings-language-description', 'languageDescription');
      setSettingsHtml('settings-language-note', 'languageNote');
      runtimeLanguageInput.querySelector('option[value="EN"]').textContent = translateSettings('languageEnglish');
      runtimeLanguageInput.querySelector('option[value="KO"]').textContent = translateSettings('languageKorean');
      setSettingsText('settings-theme-title', 'themeTitle');
      setSettingsText('settings-theme-description', 'themeDescription');
      setSettingsHtml('settings-theme-note', 'themeNote');
      runtimeThemeInput.querySelector('option[value="light"]').textContent = translateSettings('themeLight');
      runtimeThemeInput.querySelector('option[value="dark"]').textContent = translateSettings('themeDark');
      setSettingsText('settings-repo-root-title', 'repoRootTitle');
      setSettingsText('settings-repo-root-description', 'repoRootDescription');
      setSettingsHtml('settings-repo-root-note', 'repoRootNote');
      setSettingsText('settings-repo-depth-title', 'repoDepthTitle');
      setSettingsText('settings-repo-depth-description', 'repoDepthDescription');
      setSettingsHtml('settings-repo-depth-note', 'repoDepthNote');
      setSettingsText('settings-help-heading', 'settingsHelpHeading');
      setSettingsText('settings-help-description', 'settingsHelpDescription');
      setSettingsText('settings-help-card-title', 'settingsHelpCardTitle');
      setSettingsText('settings-help-card-desc', 'settingsHelpCardDesc');
      const restartBtn = document.getElementById('restart-onboarding-btn');
      if (restartBtn) restartBtn.textContent = translateSettings('settingsHelpCardBtn');
      if (btnBrowseRepoRoot) btnBrowseRepoRoot.textContent = translateSettings('dirPickerOpen');
      setSettingsText('directory-picker-title', 'dirPickerTitle');
      setSettingsText('directory-picker-description', 'dirPickerDesc');
      if (btnDirectoryPickerSelect) btnDirectoryPickerSelect.textContent = translateSettings('dirPickerSelect');
      if (btnDirectoryPickerClose) btnDirectoryPickerClose.textContent = translateSettings('dirPickerClose');
      setSettingsText('settings-slack-title', 'slackTitle');
      setSettingsText('settings-slack-description', 'slackDescription');
      setSettingsText('settings-slack-basics-title', 'slackBasicsTitle');
      setSettingsText('settings-slack-basics-description', 'slackBasicsDescription');
      setSettingsText('settings-slack-enabled-label', 'slackEnabledLabel');
      setSettingsText('settings-slack-socket-mode-label', 'slackSocketModeLabel');
      setSettingsText('settings-slack-mention-label', 'slackMentionLabel');
      setSettingsHtml('settings-slack-note', 'slackNote');
      setSettingsText('settings-slack-save-note-title', 'slackSaveNoteTitle');
      setSettingsHtml('settings-slack-save-note', 'slackSaveNote');
      setSettingsText('settings-slack-bot-token-label', 'slackBotTokenLabel');
      setSettingsText('settings-slack-app-token-label', 'slackAppTokenLabel');
      setSettingsText('settings-slack-channel-label', 'slackChannelLabel');
      setSettingsHtml('settings-slack-channel-description', 'slackChannelDescription');
      setSettingsText('settings-slack-effective-channel-label', 'slackEffectiveChannelLabel');
      setSettingsHtml('settings-slack-effective-channel-help', 'slackEffectiveChannelHelp');
      setSettingsHtml('settings-slack-channel-note', 'slackChannelNote');
      setSettingsText('settings-slack-advanced-title', 'slackAdvancedTitle');
      setSettingsText('settings-slack-advanced-description', 'slackAdvancedDescription');
      setSettingsText('settings-slack-advanced-note', 'slackAdvancedNote');
      setSettingsText('settings-slack-test-description', 'slackTestDescription');
      if (slackListenerStatus) slackListenerStatus.textContent = translateSettings('slackListenerIdle');
      clearSlackBotTokenButton.textContent = translateSettings('slackClearToken');
      clearSlackAppTokenButton.textContent = translateSettings('slackClearToken');
      testSlackSettingsButton.textContent = translateSettings('slackTestButton');
      startSlackReceiveTestButton.textContent = translateSettings('slackReceiveTestButton');
      copySlackReceiveTestButton.textContent = translateSettings('slackReceiveCopyButton');
      setSettingsText('settings-planner-title', 'plannerTitle');
      setSettingsText('settings-planner-description', 'plannerDescription');
      setSettingsText('settings-request-draft-title', 'requestDraftTitle');
      setSettingsText('settings-request-draft-description', 'requestDraftDescription');
      setSettingsText('settings-plan-approval-title', 'planApprovalTitle');
      setSettingsText('settings-plan-approval-description', 'planApprovalDescription');
      setSettingsText('settings-implementer-title', 'implementerTitle');
      setSettingsText('settings-implementer-description', 'implementerDescription');
      setSettingsText('settings-reviewer-title', 'reviewerTitle');
      setSettingsText('settings-reviewer-description', 'reviewerDescription');
      setSettingsText('settings-commit-title', 'commitTitle');
      setSettingsText('settings-commit-description', 'commitDescription');
      ['planner', 'request-draft', 'plan-approval', 'implementer', 'reviewer', 'commit'].forEach((prefix) => {
        const assistantLabel = document.getElementById(`settings-${prefix}-assistant-label`);
        if (assistantLabel) assistantLabel.textContent = translateSettings('roleAssistantLabel');
        const modelLabel = document.getElementById(`settings-${prefix}-model-label`);
        if (modelLabel) modelLabel.textContent = translateSettings('modelLabel');
        const tokenLabel = document.getElementById(`settings-${prefix}-token-label`);
        if (tokenLabel) tokenLabel.textContent = translateSettings('tokenLabel');
      });
      setSettingsText('settings-planner-agents-label', 'agentsLabel');
      setSettingsText('settings-implementer-agents-label', 'agentsLabel');
      setSettingsText('settings-reviewer-agents-label', 'agentsLabel');
      setSettingsHtml('settings-planner-note', 'plannerNote');
      setSettingsHtml('settings-request-draft-note', 'requestDraftNote');
      setSettingsHtml('settings-plan-approval-note', 'planApprovalNote');
      setSettingsHtml('settings-implementer-note', 'implementerNote');
      setSettingsHtml('settings-reviewer-note', 'reviewerNote');
      setSettingsHtml('settings-commit-note', 'commitNote');
      renderAssistantOptions(cachedAssistantOptions, runtimeCodingAssistantInput.value || 'opencode');
      cancelSettingsButton.textContent = translateSettings('cancel');
      saveSettingsButton.textContent = translateSettings('saveSettings');
      if (lastSettingsPayload) {
        applySlackSettingsData(lastSettingsPayload, { preserveInputs: true });
      } else {
        updateSlackTokenStatus(slackBotTokenStatus, null, false);
        updateSlackTokenStatus(slackAppTokenStatus, null, false);
        setSlackSettingsTestStatus(null);
        updateSlackChannelState();
      }
      if (lastSettingsPayload) {
        updateModelDiscoverySummary(lastSettingsPayload);
      } else {
        settingsDiscoverySummary.textContent = translateSettings('discoveryIdle');
        setSettingsStatus(translateSettings('statusIdle'));
      }
      renderAllRoleModelOptions();
      renderBoardPhaseTabs();
    }

    function applyRequestTranslations() {
      openComposerButton.textContent = translateRequest('openComposer');
      refreshButton.textContent = translateRequest('refreshBoard');
      setRequestText('request-modal-title', 'title');
      setRequestHtml('request-modal-description', 'description');
      setRequestText('request-copy-title', 'copyTitle');
      setRequestHtml('request-copy-body', 'copyBody');
      setRequestText('request-basics-heading', 'basicsHeading');
      setRequestText('request-basics-description', 'basicsDescription');
      setRequestText('request-repo-heading', 'repoHeading');
      setRequestText('request-repo-description', 'repoDescription');
      setRequestText('request-title-label', 'titleLabel');
      setRequestText('request-title-description', 'titleDescription');
      setRequestText('request-goal-label', 'goalLabel');
      setRequestText('request-goal-description', 'goalDescription');
      setRequestText('request-background-label', 'backgroundLabel');
      setRequestText('request-background-description', 'backgroundDescription');
      setRequestText('request-target-repo-label', 'targetRepoLabel');
      setRequestText('request-target-repo-description', 'targetRepoDescription');
      setRequestText('request-base-branch-label', 'baseBranchLabel');
      setRequestText('request-plan-auto-approve-label', 'planAutoApproveLabel');
      setRequestText('request-plan-auto-approve-description', 'planAutoApproveDescription');
      setRequestText('request-scope-label', 'scopeLabel');
      setRequestText('request-scope-description', 'scopeDescription');
      setRequestText('request-out-of-scope-label', 'outOfScopeLabel');
      setRequestText('request-out-of-scope-description', 'outOfScopeDescription');
      setRequestText('request-constraints-label', 'constraintsLabel');
      setRequestText('request-constraints-description', 'constraintsDescription');
      setRequestText('request-references-label', 'referencesLabel');
      setRequestText('request-references-description', 'referencesDescription');
      setRequestText('request-acceptance-label', 'acceptanceLabel');
      setRequestText('request-acceptance-description', 'acceptanceDescription');
      requestComposerTabs.setAttribute('aria-label', translateRequest('composerTabsLabel'));
      setRequestText('request-draft-heading', 'draftHeading');
      setRequestText('request-draft-description', 'draftDescription');
      setRequestText('request-drafts-title', 'draftsTitle');
      setRequestText('request-drafts-description', 'draftsDescription');
      requestComposerTabFields.textContent = translateRequest('composerTabFields');
      requestComposerTabAssistant.textContent = translateRequest('composerTabAssistant');
      setRequestText('request-draft-title', 'draftTitle');
      requestDraftInput.placeholder = translateRequest('draftPlaceholder');
      attachRequestDraftImageButton.textContent = translateRequest('draftAttachImage');
      sendRequestDraftButton.textContent = translateRequest('draftSend');
      cancelComposerButton.textContent = translateRequest('close');
      submitButton.textContent = translateRequest('submit');
      if (btnBrowseTargetRepo) {
        btnBrowseTargetRepo.textContent = translateSettings('dirPickerOpen');
      }
      renderRequestDrafts();
      updateRequestDraftPanel();
    }

    function refreshRequestDerivedText() {
      if (normalizeRepoPath(targetRepoInput.value)) {
        queueTargetRepoBranchLookup();
      } else {
        updateBaseBranchHelp(translateRequest('baseBranchHelp'));
      }
      applyRepoDefaults();
      validateForm();
    }

    function resolveAssistantOptions(payload) {
      const defaults = [
        { value: 'antigravity', label: 'Antigravity CLI' },
        { value: 'codex', label: 'Codex CLI' },
        { value: 'claude', label: 'Claude Code' },
        { value: 'gemini', label: 'Gemini CLI' },
        { value: 'opencode', label: 'OpenCode' },
      ];
      const configured = Array.isArray(payload?.available_assistants) ? payload.available_assistants : [];
      const availabilityByBackend = payload?.backend_availability_by_backend;
      if (!availabilityByBackend || typeof availabilityByBackend !== 'object') {
        return configured.length ? configured : defaults;
      }
      const labelByValue = new Map(defaults.map((item) => [item.value, item.label]));
      configured.forEach((item) => {
        if (!item || !item.value) return;
        labelByValue.set(item.value, item.label || labelByValue.get(item.value) || item.value);
      });
      const available = Object.entries(availabilityByBackend)
        .filter(([, status]) => status && status.available)
        .map(([value]) => ({ value, label: labelByValue.get(value) || value }));
      return available.length ? available : (configured.length ? configured : defaults);
    }

    function renderAssistantOptions(options, selectedValue = 'opencode') {
      const items = Array.isArray(options) && options.length ? options : (cachedAssistantOptions || resolveAssistantOptions(lastSettingsPayload));
      const previousRoleSelections = Object.fromEntries(roleSettingConfigs.map(({ role, backendInput }) => [role, backendInput.value || 'default']));
      runtimeCodingAssistantInput.innerHTML = items
        .map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`)
        .join('');
      runtimeCodingAssistantInput.value = items.some((item) => item.value === selectedValue) ? selectedValue : items[0].value;
      const roleOptions = [
        { value: 'default', label: translateSettings('defaultAssistantOption') },
        ...items,
      ];
      roleSettingConfigs.forEach(({ backendInput }) => {
        backendInput.innerHTML = roleOptions
          .map((item) => `<option value="${escapeHtml(item.value)}">${escapeHtml(item.label)}</option>`)
          .join('');
      });
      roleSettingConfigs.forEach(({ role, backendInput }) => {
        const nextValue = previousRoleSelections[role] || 'default';
        backendInput.value = roleOptions.some((item) => item.value === nextValue) ? nextValue : 'default';
      });
    }

    function roleSettingConfig(role) {
      return roleSettingConfigs.find((item) => item.role === role) || null;
    }

    function currentRoleModelValue(config) {
      if (!config) return '';
      const selectedValue = config.modelSelectInput?.value || '';
      if (selectedValue === customModelOptionValue) return config.modelInput?.value || '';
      if ((!selectedValue || !config.modelSelectInput?.options?.length) && !config.modelInput?.disabled && config.modelInput?.value) {
        return config.modelInput.value;
      }
      return selectedValue;
    }

    function applyRoleModelSelection(config, modelValue = '') {
      if (!config || !config.modelSelectInput || !config.modelInput) return;
      const normalizedValue = typeof modelValue === 'string' ? modelValue : '';
      const knownOptions = Array.from(config.modelSelectInput.options).map((option) => option.value);
      const useCustom = normalizedValue === customModelOptionValue || (Boolean(normalizedValue) && !knownOptions.includes(normalizedValue));
      config.modelSelectInput.value = useCustom
        ? customModelOptionValue
        : (knownOptions.includes(normalizedValue) ? normalizedValue : '');
      config.modelInput.hidden = !useCustom;
      config.modelInput.disabled = !useCustom;
      config.modelInput.value = useCustom && normalizedValue !== customModelOptionValue ? normalizedValue : '';
      config.modelInput.placeholder = translateSettings('modelCustomPlaceholder');
    }

    function renderRoleModelDatalist(config, items) {
      if (!config?.modelOptionsInput) return;
      config.modelOptionsInput.innerHTML = items
        .map((item) => `<option value="${escapeHtml(item)}"></option>`)
        .join('');
    }

    function setRoleModelValue(role, modelValue = '') {
      const config = roleSettingConfig(role);
      if (!config) return;
      applyRoleModelSelection(config, modelValue);
    }

    function resetRoleModelSelection(config) {
      applyRoleModelSelection(config, '');
    }

    function effectiveRoleBackend(role) {
      const config = roleSettingConfigs.find((item) => item.role === role);
      if (!config) return runtimeCodingAssistantInput.value || 'opencode';
      const selected = config.backendInput.value || 'default';
      return selected === 'default' ? (runtimeCodingAssistantInput.value || 'opencode') : selected;
    }

    function renderRoleModelOptions(role) {
      const config = roleSettingConfig(role);
      if (!config) return;
      const currentValue = currentRoleModelValue(config);
      const backend = effectiveRoleBackend(role);
      const modelsByBackend = lastSettingsPayload?.available_models_by_backend || {};
      const items = Array.isArray(modelsByBackend[backend]) ? [...modelsByBackend[backend]] : [];
      if (currentValue && !items.includes(currentValue) && items.length === 0) items.push(currentValue);
      renderRoleModelDatalist(config, items);
      config.modelSelectInput.innerHTML = [
        `<option value="">${escapeHtml(translateSettings('modelDefaultOption'))}</option>`,
        ...items.map((item) => `<option value="${escapeHtml(item)}">${escapeHtml(item)}</option>`),
        `<option value="${escapeHtml(customModelOptionValue)}">${escapeHtml(translateSettings('modelCustomOption'))}</option>`,
      ].join('');
      applyRoleModelSelection(config, currentValue);
    }

    function renderAllRoleModelOptions() {
      roleSettingConfigs.forEach(({ role }) => renderRoleModelOptions(role));
    }

    function updateWorkerLiveLogsControlVisibility() {
      const field = document.getElementById('settings-live-logs-field');
      const isOpenCode = runtimeCodingAssistantInput.value === 'opencode';
      if (!isOpenCode) workerLiveLogsModeInput.value = 'false';
      workerLiveLogsModeInput.disabled = !isOpenCode;
      field.hidden = !isOpenCode;
      field.style.display = isOpenCode ? '' : 'none';
    }

    function captureSettingsState() {
      return {
        language: runtimeLanguageInput.value || 'EN',
        theme: runtimeThemeInput.value || 'light',
        coding_assistant: runtimeCodingAssistantInput.value || 'opencode',
        slack_enabled: slackEnabledInput.checked,
        slack_socket_mode_enabled: slackSocketModeEnabledInput.checked,
        slack_app_mention_enabled: slackAppMentionEnabledInput.checked,
        slack_bot_token: slackBotTokenInput.value || '',
        slack_app_token: slackAppTokenInput.value || '',
        slack_default_channel: slackDefaultChannelInput.value || '',
        role_backends: {
          planner: plannerBackendInput.value === 'default' ? null : plannerBackendInput.value,
          request_draft: requestDraftBackendInput.value === 'default' ? null : requestDraftBackendInput.value,
          plan_approval: planApprovalBackendInput.value === 'default' ? null : planApprovalBackendInput.value,
          implementer: implementerBackendInput.value === 'default' ? null : implementerBackendInput.value,
          reviewer: reviewerBackendInput.value === 'default' ? null : reviewerBackendInput.value,
          commit: commitBackendInput.value === 'default' ? null : commitBackendInput.value,
        },
        worker_live_logs_enabled: workerLiveLogsModeInput.value === 'true',
        repo_discovery_root: repoDiscoveryRootInput.value || '../',
        repo_discovery_max_depth: readNumericSettingInput(repoDiscoveryMaxDepthInput, 1),
        planner_model: currentRoleModelValue(roleSettingConfig('planner')),
        request_draft_model: currentRoleModelValue(roleSettingConfig('request_draft')),
        planner_session_token_budget: readNumericSettingInput(plannerSessionTokenBudgetInput, 250),
        planner_agent_count: readNumericSettingInput(plannerAgentCountInput, 1),
        plan_approval_model: currentRoleModelValue(roleSettingConfig('plan_approval')),
        plan_approval_session_token_budget: readNumericSettingInput(planApprovalSessionTokenBudgetInput, 250),
        implementer_model: currentRoleModelValue(roleSettingConfig('implementer')),
        implementer_session_token_budget: readNumericSettingInput(implementerSessionTokenBudgetInput, 250),
        implementer_agent_count: readNumericSettingInput(implementerAgentCountInput, 1),
        reviewer_model: currentRoleModelValue(roleSettingConfig('reviewer')),
        reviewer_session_token_budget: readNumericSettingInput(reviewerSessionTokenBudgetInput, 250),
        reviewer_agent_count: readNumericSettingInput(reviewerAgentCountInput, 1),
        commit_model: currentRoleModelValue(roleSettingConfig('commit')),
        commit_session_token_budget: readNumericSettingInput(commitSessionTokenBudgetInput, 250),
      };
    }

    function updateSlackTokenStatus(element, maskedValue, configured) {
      if (!element) return;
      const input = element === slackBotTokenStatus ? slackBotTokenInput : slackAppTokenInput;
      const clearRequested = element === slackBotTokenStatus ? slackBotTokenClearRequested : slackAppTokenClearRequested;
      if (clearRequested) {
        element.textContent = translateSettings('slackTokenWillClear');
        return;
      }
      if (input && input.value) {
        element.textContent = translateSettings('slackTokenWillReplace');
        return;
      }
      if (configured && maskedValue) {
        element.textContent = translateSettings('slackTokenConfigured', { masked: maskedValue });
        return;
      }
      element.textContent = translateSettings('slackTokenNotConfigured');
    }

    function normalizeSlackChannelValue(value) {
      return typeof value === 'string' ? value.trim() : '';
    }

    function currentSlackEffectiveChannelDisplay() {
      const savedDisplay = normalizeSlackChannelValue(lastSettingsPayload?.slack_default_channel_display);
      const savedChannel = normalizeSlackChannelValue(lastSettingsPayload?.slack_default_channel);
      if (savedDisplay && savedChannel && savedDisplay !== savedChannel) return `${savedDisplay} · ${savedChannel}`;
      return savedDisplay || savedChannel;
    }

    function updateSlackChannelState() {
      if (!slackEffectiveChannelValue || !slackPendingChannelStatus) return;
      const savedChannel = normalizeSlackChannelValue(lastSettingsPayload?.slack_default_channel_display) || normalizeSlackChannelValue(lastSettingsPayload?.slack_default_channel);
      const effectiveLabel = currentSlackEffectiveChannelDisplay();
      const draftChannel = normalizeSlackChannelValue(slackDefaultChannelInput?.value);
      slackEffectiveChannelValue.textContent = effectiveLabel || translateSettings('slackChannelUnset');
      if (!savedChannel) {
        slackPendingChannelStatus.hidden = false;
        slackPendingChannelStatus.removeAttribute('data-tone');
        if (draftChannel) {
          slackPendingChannelStatus.innerHTML = translateSettings('slackChannelPendingChange', { channel: escapeHtml(draftChannel) });
        } else {
          slackPendingChannelStatus.innerHTML = translateSettings('slackChannelPendingFirst');
        }
        return;
      }
      if (!draftChannel) {
        slackPendingChannelStatus.hidden = false;
        slackPendingChannelStatus.dataset.tone = 'error';
        slackPendingChannelStatus.innerHTML = translateSettings('slackChannelPendingClear', { channel: escapeHtml(savedChannel) });
        return;
      }
      if (draftChannel === savedChannel) {
        slackPendingChannelStatus.hidden = true;
        slackPendingChannelStatus.removeAttribute('data-tone');
        slackPendingChannelStatus.innerHTML = translateSettings('slackChannelPendingNone');
        return;
      }
      slackPendingChannelStatus.hidden = false;
      slackPendingChannelStatus.dataset.tone = 'success';
      slackPendingChannelStatus.innerHTML = translateSettings('slackChannelPendingChange', { channel: escapeHtml(draftChannel) });
    }

    function setSlackSettingsTestStatus(result, tone = 'neutral') {
      if (!slackSettingsTestStatus) return;
      if (!result) {
        slackSettingsTestStatus.hidden = true;
        slackSettingsTestStatus.textContent = '';
        slackSettingsTestStatus.removeAttribute('data-tone');
        return;
      }
      const lines = [];
      if (result.summary) lines.push(result.summary);
      if (Array.isArray(result.checks)) {
        result.checks.forEach((check) => {
          const prefix = check.ok ? '✓' : '✗';
          lines.push(`${prefix} ${check.message}`);
        });
      }
      slackSettingsTestStatus.textContent = lines.join('\n');
      slackSettingsTestStatus.hidden = false;
      if (tone === 'neutral') {
        slackSettingsTestStatus.removeAttribute('data-tone');
      } else {
        slackSettingsTestStatus.dataset.tone = tone;
      }
    }

    function updateSlackReceiveControls() {
      if (copySlackReceiveTestButton) copySlackReceiveTestButton.hidden = !lastSlackReceiveInstruction;
    }

    function updateSlackReceiveTestStatus(snapshot) {
      if (!slackReceiveTestStatus) return;
      if (!snapshot) {
        lastSlackReceiveInstruction = '';
        if (slackListenerStatus) slackListenerStatus.textContent = translateSettings('slackListenerIdle');
        slackReceiveTestStatus.hidden = true;
        slackReceiveTestStatus.textContent = '';
        slackReceiveTestStatus.removeAttribute('data-tone');
        updateSlackReceiveControls();
        return;
      }
      const lines = [];
      let listenerSummary = translateSettings('slackListenerIdle');
      if (snapshot.listener_connected) {
        listenerSummary = translateSettings('slackListenerConnected');
        lines.push(translateSettings('slackListenerConnectedDetail'));
      } else if (snapshot.listener_enabled) {
        listenerSummary = translateSettings('slackListenerStarting');
        lines.push(translateSettings('slackListenerStartingDetail'));
      } else {
        listenerSummary = translateSettings('slackListenerIdle');
        lines.push(translateSettings('slackListenerIdleDetail'));
      }
      if (snapshot.listener_last_error) {
        listenerSummary = translateSettings('slackListenerError');
        lines.push(translateSettings('slackListenerLastError', { error: snapshot.listener_last_error }));
      }
      if (slackListenerStatus) slackListenerStatus.textContent = listenerSummary;
      const receiveTest = snapshot.receive_test;
      let tone = 'neutral';
      let showStatus = Boolean(snapshot.listener_last_error);
      if (receiveTest) {
        showStatus = true;
        if (receiveTest.status === 'pending') {
          tone = 'neutral';
          lines.push(translateSettings('slackReceiveWaiting'));
          lines.push(translateSettings('slackReceiveInstruction', { instruction: receiveTest.instruction }));
          lastSlackReceiveInstruction = receiveTest.instruction && receiveTest.instruction !== translateSettings('slackReceivePreparing') ? receiveTest.instruction : '';
        } else if (receiveTest.status === 'received') {
          tone = 'success';
          lines.push(translateSettings('slackReceiveMatched'));
          lastSlackReceiveInstruction = receiveTest.instruction || lastSlackReceiveInstruction;
          if (receiveTest.channel) lines.push(`Channel: ${receiveTest.channel}`);
          if (receiveTest.user) lines.push(`User: ${receiveTest.user}`);
        } else {
          tone = 'error';
          lastSlackReceiveInstruction = receiveTest.instruction || '';
          lines.push(receiveTest.error || 'Slack receive test expired without a matching mention.');
        }
      }
      updateSlackReceiveControls();
      if (slackAdvancedDetails && showStatus) slackAdvancedDetails.open = true;
      if (!showStatus) {
        slackReceiveTestStatus.hidden = true;
        slackReceiveTestStatus.textContent = '';
        slackReceiveTestStatus.removeAttribute('data-tone');
        return;
      }
      slackReceiveTestStatus.textContent = lines.join('\n');
      slackReceiveTestStatus.hidden = false;
      if (tone === 'neutral') {
        slackReceiveTestStatus.removeAttribute('data-tone');
      } else {
        slackReceiveTestStatus.dataset.tone = tone;
      }
    }

    function buildSlackSettingsPayload() {
      const payload = {
        slack_enabled: slackEnabledInput.checked,
        slack_socket_mode_enabled: slackSocketModeEnabledInput.checked,
        slack_default_channel: slackDefaultChannelInput.value,
        slack_app_mention_enabled: slackAppMentionEnabledInput.checked,
      };
      if (slackBotTokenClearRequested || slackBotTokenInput.value) {
        payload.slack_bot_token = slackBotTokenClearRequested ? '' : slackBotTokenInput.value;
      }
      if (slackAppTokenClearRequested || slackAppTokenInput.value) {
        payload.slack_app_token = slackAppTokenClearRequested ? '' : slackAppTokenInput.value;
      }
      return payload;
    }

    function hasUnsavedSlackReceiveSettings() {
      if (!lastSettingsPayload) return false;
      if (slackBotTokenInput.value || slackAppTokenInput.value) return true;
      if (slackBotTokenClearRequested || slackAppTokenClearRequested) return true;
      return (
        slackEnabledInput.checked !== Boolean(lastSettingsPayload.slack_enabled)
        || slackSocketModeEnabledInput.checked !== (lastSettingsPayload.slack_socket_mode_enabled !== false)
        || slackAppMentionEnabledInput.checked !== Boolean(lastSettingsPayload.slack_app_mention_enabled)
        || (slackDefaultChannelInput.value || '') !== (lastSettingsPayload.slack_default_channel_display || lastSettingsPayload.slack_default_channel || '')
      );
    }

    function hasUnsavedSlackChannelActivationDependencies() {
      if (!lastSettingsPayload) return false;
      if (slackBotTokenInput.value || slackAppTokenInput.value) return true;
      if (slackBotTokenClearRequested || slackAppTokenClearRequested) return true;
      return (
        slackEnabledInput.checked !== Boolean(lastSettingsPayload.slack_enabled)
        || slackSocketModeEnabledInput.checked !== (lastSettingsPayload.slack_socket_mode_enabled !== false)
        || slackAppMentionEnabledInput.checked !== Boolean(lastSettingsPayload.slack_app_mention_enabled)
      );
    }

    async function pollSlackReceiveTestStatus() {
      if (slackReceiveTestPollTimer) {
        clearTimeout(slackReceiveTestPollTimer);
        slackReceiveTestPollTimer = null;
      }
      if (settingsModal.hidden) return;
      try {
        const response = await fetch('/api/settings/slack-receive-test');
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Failed to load Slack receive test status.');
        updateSlackReceiveTestStatus(data);
        const status = data.receive_test?.status;
        if (status === 'pending') {
          slackReceiveTestPollTimer = setTimeout(() => { pollSlackReceiveTestStatus().catch(() => {}); }, 2000);
        }
      } catch (error) {
        updateSlackReceiveTestStatus({ listener_enabled: false, listener_connected: false, listener_last_error: error.message, receive_test: null });
      }
    }

    async function copySlackReceiveTestInstruction() {
      if (!lastSlackReceiveInstruction) {
        updateSlackReceiveTestStatus({ listener_enabled: false, listener_connected: false, listener_last_error: translateSettings('slackReceiveCopyUnavailable'), receive_test: null });
        return;
      }
      try {
        await navigator.clipboard.writeText(lastSlackReceiveInstruction);
        updateSlackReceiveTestStatus({ listener_enabled: true, listener_connected: true, listener_last_error: null, receive_test: { status: 'pending', instruction: lastSlackReceiveInstruction, error: null } });
        slackReceiveTestStatus.dataset.tone = 'success';
        slackReceiveTestStatus.textContent = `${translateSettings('slackReceiveCopySuccess')}\n${translateSettings('slackReceiveInstruction', { instruction: lastSlackReceiveInstruction })}`;
      } catch (_error) {
        slackReceiveTestStatus.hidden = false;
        slackReceiveTestStatus.dataset.tone = 'error';
        slackReceiveTestStatus.textContent = `${translateSettings('slackReceiveCopyFailed')}\n${translateSettings('slackReceiveInstruction', { instruction: lastSlackReceiveInstruction })}`;
      }
    }

    function applySlackSettingsData(data, { preserveInputs = false, preserveChannelInput = false } = {}) {
      slackEnabledInput.checked = Boolean(data.slack_enabled);
      slackSocketModeEnabledInput.checked = data.slack_socket_mode_enabled !== false;
      slackAppMentionEnabledInput.checked = Boolean(data.slack_app_mention_enabled);
      if (!preserveChannelInput) {
        slackDefaultChannelInput.value = data.slack_default_channel_display || data.slack_default_channel || '';
      }
      if (!preserveInputs) {
        slackBotTokenClearRequested = false;
        slackAppTokenClearRequested = false;
        slackBotTokenInput.value = '';
        slackAppTokenInput.value = '';
      }
      updateSlackTokenStatus(slackBotTokenStatus, data.slack_bot_token_masked, data.slack_bot_token_configured);
      updateSlackTokenStatus(slackAppTokenStatus, data.slack_app_token_masked, data.slack_app_token_configured);
      updateSlackReceiveTestStatus(data.slack_runtime || null);
      updateSlackChannelState();
    }

    function restoreSettingsState(state) {
      if (!state) return;
      runtimeLanguageInput.value = state.language || initialRuntimeLanguage;
      runtimeLanguageInput.dispatchEvent(new Event('change'));
      runtimeThemeInput.value = state.theme || initialRuntimeTheme;
      applyRuntimeTheme(runtimeThemeInput.value);
      renderAssistantOptions(cachedAssistantOptions, state.coding_assistant || 'opencode');
      slackEnabledInput.checked = Boolean(state.slack_enabled);
      slackSocketModeEnabledInput.checked = state.slack_socket_mode_enabled !== false;
      slackAppMentionEnabledInput.checked = Boolean(state.slack_app_mention_enabled);
      slackBotTokenInput.value = state.slack_bot_token || '';
      slackAppTokenInput.value = state.slack_app_token || '';
      slackBotTokenClearRequested = false;
      slackAppTokenClearRequested = false;
      slackDefaultChannelInput.value = state.slack_default_channel || '';
      updateSlackChannelState();
      plannerBackendInput.value = state.role_backends?.planner || 'default';
      requestDraftBackendInput.value = state.role_backends?.request_draft || 'default';
      planApprovalBackendInput.value = state.role_backends?.plan_approval || 'default';
      implementerBackendInput.value = state.role_backends?.implementer || 'default';
      reviewerBackendInput.value = state.role_backends?.reviewer || 'default';
      commitBackendInput.value = state.role_backends?.commit || 'default';
      updateWorkerLiveLogsControlVisibility();
      workerLiveLogsModeInput.value = state.worker_live_logs_enabled ? 'true' : 'false';
      repoDiscoveryRootInput.value = state.repo_discovery_root || '../';
      syncNumericSettingInput(repoDiscoveryMaxDepthInput, state.repo_discovery_max_depth, 2);
      setRoleModelValue('planner', state.planner_model || '');
      setRoleModelValue('request_draft', state.request_draft_model || '');
      syncNumericSettingInput(plannerSessionTokenBudgetInput, state.planner_session_token_budget, 250);
      syncNumericSettingInput(plannerAgentCountInput, state.planner_agent_count, 1);
      setRoleModelValue('plan_approval', state.plan_approval_model || '');
      syncNumericSettingInput(planApprovalSessionTokenBudgetInput, state.plan_approval_session_token_budget, 250);
      setRoleModelValue('implementer', state.implementer_model || '');
      syncNumericSettingInput(implementerSessionTokenBudgetInput, state.implementer_session_token_budget, 250);
      syncNumericSettingInput(implementerAgentCountInput, state.implementer_agent_count, 1);
      setRoleModelValue('reviewer', state.reviewer_model || '');
      syncNumericSettingInput(reviewerSessionTokenBudgetInput, state.reviewer_session_token_budget, 250);
      syncNumericSettingInput(reviewerAgentCountInput, state.reviewer_agent_count, 1);
      setRoleModelValue('commit', state.commit_model || '');
      syncNumericSettingInput(commitSessionTokenBudgetInput, state.commit_session_token_budget, 250);
      renderAllRoleModelOptions();
      applyRuntimeSettingsTranslations();
      applyRequestTranslations();
      applyHumanReviewTranslations();
      refreshRequestDerivedText();
    }

    function applyRuntimeTheme(theme) {
      body.dataset.theme = theme === 'dark' ? 'dark' : 'light';
    }

    function closeSettingsModal({ restore = false } = {}) {
      if (restore) restoreSettingsState(lastLoadedSettingsState);
      setSettingsModalOpen(false);
    }
    window.closeSettingsModal = closeSettingsModal;

    function phaseLabel(phase) {
      const labels = currentUiLanguage() === 'KO'
        ? {
            plan: '플랜 단계',
            implementation: '구현 단계',
            final: '최종 완료',
            closed: '취소됨',
          }
        : {
            plan: 'Planning',
            implementation: 'Implementation',
            final: 'Completed',
            closed: 'Closed',
          };
      return labels[phase] || phase;
    }

    function boardPhaseForState(state) {
      return Object.entries(boardPhaseStates).find(([, states]) => states.includes(state))?.[0] || '';
    }

    function shouldShowBoardPhaseCount(phase) {
      return phase === 'plan' || phase === 'implementation' || phase === 'closed';
    }

    function boardPhaseCountLabel(phase, count) {
      if (currentUiLanguage() === 'KO') {
        return `${phaseLabel(phase)}에 ${count}개 태스크`;
      }
      return `${count} ${count === 1 ? 'task' : 'tasks'} in ${phaseLabel(phase)}`;
    }

    function renderBoardPhaseTabs() {
      boardPhaseTabs.querySelectorAll('[data-board-phase]').forEach((button) => {
        const phase = button.dataset.boardPhase;
        const label = phaseLabel(phase);
        const count = boardPhaseTaskCounts[phase] || 0;
        button.innerHTML = shouldShowBoardPhaseCount(phase)
          ? `<span class="board-phase-tab-label">${escapeHtml(label)}</span><span class="board-phase-tab-count" aria-hidden="true">${escapeHtml(String(count))}</span>`
          : escapeHtml(label);
        button.setAttribute('aria-label', shouldShowBoardPhaseCount(phase) ? boardPhaseCountLabel(phase, count) : label);
        button.classList.toggle('active', phase === activeBoardPhase);
      });
    }

    function selectDefaultBoardPhase(columns) {
      const itemCounts = Object.fromEntries(columns.map((column) => [column.state, Array.isArray(column.items) ? column.items.length : 0]));
      for (const rule of boardPhasePriorityRules) {
        if (rule.states.some((state) => (itemCounts[state] || 0) > 0)) {
          return rule.phase;
        }
      }
      return 'plan';
    }

    function setSettingsTab(tab) {
      const panels = ['general', 'slack', 'roles'];
      panels.forEach(p => {
        const active = p === tab;
        const tabEl = document.getElementById(`settings-tab-${p}`);
        const panelEl = document.getElementById(`settings-panel-${p}`);
        if (tabEl) {
          tabEl.classList.toggle('active', active);
          tabEl.setAttribute('aria-selected', String(active));
        }
        if (panelEl) {
          panelEl.hidden = !active;
        }
      });
      // Auto-scroll settings modal body to top on tab change
      const settingsScrollBody = document.querySelector('#settings-modal .modal-scroll-body');
      if (settingsScrollBody) {
        settingsScrollBody.scrollTop = 0;
      }
    }
    window.setSettingsTab = setSettingsTab;


    document.getElementById('settings-tab-general').addEventListener('click', () => setSettingsTab('general'));
    document.getElementById('settings-tab-slack').addEventListener('click', () => setSettingsTab('slack'));
    document.getElementById('settings-tab-roles').addEventListener('click', () => setSettingsTab('roles'));
