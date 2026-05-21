
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
      setSettingsText('settings-modal-title', 'settingsTitle');
      setSettingsText('settings-modal-description', 'settingsDescription');
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
        { value: 'opencode', label: 'OpenCode' },
        { value: 'codex', label: 'Codex CLI' },
        { value: 'gemini', label: 'Gemini CLI' },
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
      }
      updateSlackTokenStatus(slackBotTokenStatus, data.slack_bot_token_masked, data.slack_bot_token_configured);
      updateSlackTokenStatus(slackAppTokenStatus, data.slack_app_token_masked, data.slack_app_token_configured);
      if (!preserveInputs) {
        slackBotTokenInput.value = '';
        slackAppTokenInput.value = '';
      }
      updateSlackReceiveTestStatus(data.slack_runtime || null);
      updateSlackChannelState();
    }

    function restoreSettingsState(state) {
      if (!state) return;
      runtimeLanguageInput.value = state.language || initialRuntimeLanguage;
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

    function phaseLabel(phase) {
      const labels = currentUiLanguage() === 'KO'
        ? {
            plan: '플랜 단계',
            implementation: '구현 단계',
            final: '최종 완료',
          }
        : {
            plan: 'Planning',
            implementation: 'Implementation',
            final: 'Completed',
          };
      return labels[phase] || phase;
    }

    function renderBoardPhaseTabs() {
      boardPhaseTabs.querySelectorAll('[data-board-phase]').forEach((button) => {
        const phase = button.dataset.boardPhase;
        button.textContent = phaseLabel(phase);
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

    function applyBoardSnapshot(data) {
      saveBoardScrollPositions();
      boardTaskSnapshots = new Map(data.columns.flatMap((column) => (column.items || []).map((item) => [item.task_id, item])));
      if (!boardPhaseManuallySelected) {
        activeBoardPhase = selectDefaultBoardPhase(data.columns);
      }
      renderBoardPhaseTabs();
      const visibleStates = new Set(boardPhaseStates[activeBoardPhase] || []);
      const visibleColumns = data.columns.filter((column) => visibleStates.has(column.state));
      board.classList.toggle('implementation-board', activeBoardPhase === 'implementation');
      board.classList.toggle('plan-board', activeBoardPhase === 'plan');
      board.classList.toggle('final-board', activeBoardPhase === 'final');
      if (activeBoardPhase === 'implementation') {
        const columnsByState = new Map(visibleColumns.map((column) => [column.state, column]));
        board.innerHTML = implementationBoardRows.map((states) => `
          <div class="implementation-board-row" style="--implementation-row-columns: ${states.length};">
            ${states.map((state) => renderBoardColumn(columnsByState.get(state) || { state, items: [] })).join('')}
          </div>`).join('');
      } else if (activeBoardPhase === 'final') {
        board.innerHTML = renderFinalBoard(visibleColumns);
      } else {
        board.innerHTML = visibleColumns.map((column) => renderBoardColumn(column)).join('');
      }
      refreshRunningClocks();
      refreshActiveTaskFromBoardSnapshot();
      restoreBoardScrollPositions();
    }

    function refreshActiveTaskFromBoardSnapshot() {
      if (taskModal.hidden || !activeTaskId || !activeTaskDetail) return;
      const snapshot = boardTaskSnapshots.get(activeTaskId);
      if (!snapshot) return;
      const activeMetadata = activeTaskDetail.metadata || {};
      const snapshotMetadata = snapshot.metadata || {};
      const stateChanged = snapshot.state !== activeMetadata.state;
      const updatedAtChanged = Boolean(snapshot.updated_at) && snapshot.updated_at !== activeMetadata.updated_at;
      if (!stateChanged && !updatedAtChanged) return;
      const nextMetadata = {
        ...activeMetadata,
        ...snapshotMetadata,
        review: {
          ...(activeMetadata.review || {}),
          ...(snapshotMetadata.review || {}),
        },
        state: stateChanged ? snapshot.state : activeMetadata.state,
        updated_at: updatedAtChanged ? snapshot.updated_at : activeMetadata.updated_at,
      };
      const nextMarkdownFiles = ['plan-approving', 'waiting-check-plans'].includes(snapshot.state) && !activeTaskDetail.markdown_files.includes('PLAN.md')
        ? ['PLAN.md', ...activeTaskDetail.markdown_files]
        : activeTaskDetail.markdown_files;
      activeTaskDetail = {
        ...activeTaskDetail,
        metadata: nextMetadata,
        markdown_files: nextMarkdownFiles,
      };
      hydrateTaskModalChrome(snapshot, { preserveTab: true });
      updatePlanActionState();
      updateHumanVerificationState();
      updateTaskDeleteState();
      scheduleActiveTaskRefresh({ reloadArtifact: stateChanged || updatedAtChanged });
    }

    async function loadBoard() {
      const [boardResponse, draftResponse] = await Promise.all([
        fetch('/api/board'),
        fetch('/api/request-drafts'),
      ]);
      const boardData = await boardResponse.json();
      if (!boardResponse.ok) throw new Error(boardData.detail || 'Failed to load board.');
      applyBoardSnapshot(boardData);
      const draftData = await draftResponse.json();
      if (!draftResponse.ok) throw new Error(draftData.detail || 'Failed to load request drafts.');
      requestDraftList = Array.isArray(draftData.items) ? draftData.items : [];
      renderRequestDrafts();
    }

    let savedScrollPositions = {
      boardLeft: 0,
      boardTop: 0,
      columns: {},
      taskOverview: 0
    };

    function saveBoardScrollPositions() {
      const boardEl = document.getElementById('board');
      if (boardEl) {
        savedScrollPositions.boardLeft = boardEl.scrollLeft;
        savedScrollPositions.boardTop = boardEl.scrollTop;
      }
      savedScrollPositions.columns = {};
      const columns = document.querySelectorAll('.column');
      columns.forEach((col) => {
        let key = '';
        if (col.dataset.state) {
          key = `state:${col.dataset.state}`;
        } else if (col.dataset.projectPath) {
          key = `project:${col.dataset.projectPath}`;
        }
        if (key) {
          const cardsEl = col.querySelector('.column-cards');
          if (cardsEl) {
            savedScrollPositions.columns[key] = cardsEl.scrollTop;
          }
          const groups = col.querySelectorAll('.target-branch-group');
          groups.forEach((group) => {
            const branch = group.dataset.branch;
            if (branch) {
              const groupCardsEl = group.querySelector('.column-cards');
              if (groupCardsEl) {
                savedScrollPositions.columns[`${key}:branch:${branch}`] = groupCardsEl.scrollTop;
              }
            }
          });
        }
      });
      const taskOverviewEl = document.getElementById('task-overview');
      if (taskOverviewEl) {
        savedScrollPositions.taskOverview = taskOverviewEl.scrollTop;
      }
    }

    function restoreBoardScrollPositions() {
      const boardEl = document.getElementById('board');
      if (boardEl) {
        boardEl.scrollLeft = savedScrollPositions.boardLeft;
        boardEl.scrollTop = savedScrollPositions.boardTop;
      }
      const columns = document.querySelectorAll('.column');
      columns.forEach((col) => {
        let key = '';
        if (col.dataset.state) {
          key = `state:${col.dataset.state}`;
        } else if (col.dataset.projectPath) {
          key = `project:${col.dataset.projectPath}`;
        }
        if (key) {
          const cardsEl = col.querySelector('.column-cards');
          if (cardsEl && savedScrollPositions.columns[key] !== undefined) {
            cardsEl.scrollTop = savedScrollPositions.columns[key];
          }
          const groups = col.querySelectorAll('.target-branch-group');
          groups.forEach((group) => {
            const branch = group.dataset.branch;
            if (branch) {
              const groupCardsEl = group.querySelector('.column-cards');
              const groupKey = `${key}:branch:${branch}`;
              if (groupCardsEl && savedScrollPositions.columns[groupKey] !== undefined) {
                groupCardsEl.scrollTop = savedScrollPositions.columns[groupKey];
              }
            }
          });
        }
      });
      const taskOverviewEl = document.getElementById('task-overview');
      if (taskOverviewEl && savedScrollPositions.taskOverview !== undefined) {
        taskOverviewEl.scrollTop = savedScrollPositions.taskOverview;
      }
    }

    function boardCardLabel(key) {
      const labels = currentUiLanguage() === 'KO'
        ? {
            repo: '저장소',
            branch: '브랜치',
            runtime: '총 시간',
          }
        : {
            repo: 'Repo',
            branch: 'Branch',
            runtime: 'Total',
          };
      return labels[key] || key;
    }

    function hashText(value) {
      let hash = 0;
      const input = String(value || '');
      for (let index = 0; index < input.length; index += 1) {
        hash = ((hash << 5) - hash) + input.charCodeAt(index);
        hash |= 0;
      }
      return Math.abs(hash);
    }

    function repoTagTone(path) {
      const hash = hashText(normalizeRepoPath(path) || 'target-repo');
      const hue = hash % 360;
      const saturation = 48 + (hash % 10);
      return {
        background: `hsla(${hue}, ${saturation}%, 90%, 0.96)`,
        border: `hsla(${hue}, ${Math.max(36, saturation - 10)}%, 46%, 0.26)`,
        text: `hsl(${hue}, ${Math.max(42, saturation - 4)}%, 27%)`,
        darkAccent: `hsl(${hue}, ${Math.max(52, saturation - 2)}%, 72%)`,
        darkBackground: `hsla(${hue}, ${Math.max(44, saturation - 2)}%, 30%, 0.34)`,
        darkBorder: `hsla(${hue}, ${Math.max(40, saturation - 6)}%, 62%, 0.34)`,
        darkText: `hsl(${hue}, ${Math.max(48, saturation)}%, 78%)`,
      };
    }

    function branchIconSvg(className = 'final-branch-icon') {
      return `<svg class="${escapeHtml(className)}" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M5 2.75a2.25 2.25 0 1 1-1.5 2.121v3.258a2.251 2.251 0 0 1 0 3.742v.258a2.25 2.25 0 1 1-1 0v-.258a2.251 2.251 0 0 1 0-3.742V4.871A2.25 2.25 0 0 1 5 2.75Zm0 1.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm0 7a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Zm6-6.5a2.25 2.25 0 0 1 .5 4.444v.806A3.5 3.5 0 0 1 8 13.5H5.75a.75.75 0 0 1 0-1.5H8A2 2 0 0 0 10 10V9.194A2.25 2.25 0 1 1 11 4.75Zm0 1.5a.75.75 0 1 0 0 1.5.75.75 0 0 0 0-1.5Z" fill="currentColor"/></svg>`;
    }

    function taskIdIconSvg(className = 'card-task-id-icon') {
      return `<svg class="${escapeHtml(className)}" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M6.25 2.5a.75.75 0 0 1 .75.75V5.5h2V3.25a.75.75 0 0 1 1.5 0V5.5h2.25a.75.75 0 0 1 0 1.5H10.5v2h2.25a.75.75 0 0 1 0 1.5H10.5v2.25a.75.75 0 0 1-1.5 0V10.5H7v2.25a.75.75 0 0 1-1.5 0V10.5H3.25a.75.75 0 0 1 0-1.5H5.5V7H3.25a.75.75 0 0 1 0-1.5H5.5V3.25a.75.75 0 0 1 .75-.75Zm.75 4.5v2h2V7H7Z" fill="currentColor"/></svg>`;
    }

    function workHistoryIconSvg(className = 'card-history-icon') {
      return `<svg class="${escapeHtml(className)}" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M3.25 3.75A1.75 1.75 0 0 1 5 2h6a1.75 1.75 0 0 1 1.75 1.75v8.5A1.75 1.75 0 0 1 11 14H5a1.75 1.75 0 0 1-1.75-1.75v-8.5Z" stroke="currentColor" stroke-width="1.2"/><path d="M5.5 5.25h5M5.5 7.75h5M5.5 10.25H9" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><path d="M2.25 5.5h1.5M2.25 8h1.5M2.25 10.5h1.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>`;
    }

    function caretIconSvg(className = 'target-branch-caret') {
      return `<svg class="${escapeHtml(className)}" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M4.47 6.22a.75.75 0 0 1 1.06 0L8 8.69l2.47-2.47a.75.75 0 1 1 1.06 1.06l-3 3a.75.75 0 0 1-1.06 0l-3-3a.75.75 0 0 1 0-1.06Z" fill="currentColor"/></svg>`;
    }

    function repoIconSvg(className = 'card-repo-icon') {
      return `<svg class="${escapeHtml(className)}" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M8 1.9 2.75 4.45v7.1L8 14.1l5.25-2.55v-7.1L8 1.9Zm0 1.665 3.553 1.726L8 7.016 4.447 5.291 8 3.565Zm-3.75 2.93L7.25 7.95v4.11l-3-1.457v-4.11Zm4.5 5.565v-4.11l3-1.456v4.109l-3 1.457Z" fill="currentColor"/></svg>`;
    }

    function renderTag(label, value, className = '', style = '', title = '', prefixHtml = '') {
      if (!value) return '';
      const classes = ['card-tag'];
      if (className) classes.push(className);
      const styleAttr = style ? ` style="${style}"` : '';
      const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';
      const labelHtml = label ? `<span class="card-tag-label">${escapeHtml(label)}</span>` : '';
      return `<span class="${classes.join(' ')}"${styleAttr}${titleAttr}>${labelHtml}${prefixHtml}<span class="card-tag-value">${escapeHtml(value)}</span></span>`;
    }

    function renderCardTags(item, options = {}) {
      const { compactFinal = false } = options;
      const repoPath = normalizeRepoPath(item.target_repo_root);
      const repoLabel = item.target_repo_label || deriveRepoContext(repoPath).repoName || 'target repo';
      const branchLabel = item.base_branch || '';
      const finalBranchLabel = item.final_branch || '';
      const repoTone = repoTagTone(repoPath);
      const repoStyle = `--tag-bg:${repoTone.background};--tag-border:${repoTone.border};--tag-text:${repoTone.text};--tag-bg-dark:${repoTone.darkBackground};--tag-border-dark:${repoTone.darkBorder};--tag-text-dark:${repoTone.darkText};`;
      const tags = [
        renderTag('', item.task_id || '', 'card-tag-id', '', item.task_id || '', taskIdIconSvg()),
        compactFinal ? '' : renderTag('', repoLabel, 'card-tag-repo', repoStyle, repoPath || repoLabel, repoIconSvg('card-repo-icon')),
        compactFinal ? '' : renderTag('', branchLabel, 'card-tag-branch', '', branchLabel, branchIconSvg('card-branch-icon')),
        compactFinal ? renderTag('', finalBranchLabel, 'card-tag-branch card-tag-final-branch', '', finalBranchLabel, branchIconSvg('card-branch-icon')) : '',
      ].filter(Boolean);
      const runtimeValue = renderCardRuntime(item);
      const runtime = runtimeValue ? `<span class="card-meta card-runtime-meta">${renderCardActivity(item)}${runtimeValue}</span>` : '';
      return `<div class="card-meta-row"><div class="card-tag-row">${tags.join('')}</div>${runtime}</div>`;
    }

    function resolveTaskCompletedAt(task) {
      const segmentCompletedAt = task?.stage_timing?.segments?.filter((segment) => segment.state === 'done').slice(-1)[0]?.entered_at;
      if (segmentCompletedAt) return segmentCompletedAt;
      const historyCompletedAt = task?.history?.filter((entry) => entry.state === 'done').slice(-1)[0]?.entered_at;
      return historyCompletedAt || '';
    }

    function hexToRgba(hex, alpha) {
      const normalized = String(hex || '').replace('#', '');
      const expanded = normalized.length === 3 ? normalized.split('').map((char) => char + char).join('') : normalized;
      const value = Number.parseInt(expanded, 16);
      if (Number.isNaN(value)) return `rgba(82,96,107,${alpha})`;
      const red = (value >> 16) & 255;
      const green = (value >> 8) & 255;
      const blue = value & 255;
      return `rgba(${red},${green},${blue},${alpha})`;
    }

    function stateTagStyle(state) {
      const color = stageColor(state || 'requests');
      return `--tag-bg:${hexToRgba(color, 0.14)};--tag-border:${hexToRgba(color, 0.28)};--tag-text:${color};--tag-bg-dark:${hexToRgba(color, 0.24)};--tag-border-dark:${hexToRgba(color, 0.4)};--tag-text-dark:${color};`;
    }

    function renderTaskSubtitleTags(task) {
      if (!task?.task_id) return `<span class="card-tag">${escapeHtml(translateTask('loadingTaskDetails'))}</span>`;
      const repoPath = normalizeRepoPath(task.target_repo_root);
      const repoLabel = task.target_repo_label || deriveRepoContext(repoPath).repoName || 'target repo';
      const baseBranch = task.base_branch || '';
      const finalBranch = task.final_branch || '';
      const state = task.state || '';
      const completedAt = task.completed_at || resolveTaskCompletedAt(task);
      const repoTone = repoTagTone(repoPath);
      const repoStyle = `--tag-bg:${repoTone.background};--tag-border:${repoTone.border};--tag-text:${repoTone.text};--tag-bg-dark:${repoTone.darkBackground};--tag-border-dark:${repoTone.darkBorder};--tag-text-dark:${repoTone.darkText};`;
      const statusStyle = state ? stateTagStyle(state) : '';
      const leftTags = [
        renderTag('', task.task_id || '', 'card-tag-id', '', task.task_id || '', taskIdIconSvg()),
        renderTag('', repoLabel, 'card-tag-repo', repoStyle, repoPath || repoLabel, repoIconSvg('card-repo-icon')),
        renderTag('', baseBranch, 'card-tag-branch', '', baseBranch, branchIconSvg('card-branch-icon')),
        renderTag('', finalBranch, 'card-tag-branch card-tag-final-branch', '', finalBranch, branchIconSvg('card-branch-icon')),
      ].filter(Boolean).join('');
      const rightTags = [
        state ? renderTag('', state === 'done' && completedAt ? `${stateLabel(state)} · ${formatDateTime(completedAt)}` : stateLabel(state), 'card-tag-branch card-tag-final-branch', statusStyle, stateLabel(state), workHistoryIconSvg()) : '',
      ].filter(Boolean).join('');
      return `<div class="task-modal-tag-group">${leftTags}</div><div class="task-modal-tag-group task-modal-status-group">${rightTags}</div>`;
    }

    function renderRetrospectiveContextTags(record = null) {
      const repoPath = normalizeRepoPath(record?.target_repo_root || activeRetrospectiveTargetRepoRoot);
      const repoLabel = record?.target_repo_label || deriveRepoContext(repoPath).repoName || '';
      const baseBranch = record?.base_branch || activeRetrospectiveBaseBranch || '';
      const comparisonBranch = record?.comparison_branch || activeRetrospectiveComparisonBranch || '';
      const repoTone = repoPath ? repoTagTone(repoPath) : null;
      const repoStyle = repoTone ? `--tag-bg:${repoTone.background};--tag-border:${repoTone.border};--tag-text:${repoTone.text};` : '';
      return [
        renderTag('', repoLabel, 'card-tag-repo', repoStyle, repoPath || repoLabel, repoIconSvg('card-repo-icon')),
        renderTag('', baseBranch, 'card-tag-branch', '', baseBranch, branchIconSvg('card-branch-icon')),
        comparisonBranch ? renderTag('', comparisonBranch, 'card-tag-branch', '', `${translateTask('retrospectiveCompareTagLabel')}: ${comparisonBranch}`, branchIconSvg('card-branch-icon')) : '',
      ].filter(Boolean).join('');
    }

    function normalizedRetrospectiveComparisonBranch() {
      return (retrospectiveCompareBranchInput.value || '').trim();
    }

    async function loadRetrospectiveCompareBranchOptions(targetRepoRoot, baseBranch) {
      const currentValue = normalizedRetrospectiveComparisonBranch();
      retrospectiveCompareOptions.innerHTML = '';
      if (!targetRepoRoot) return;
      try {
        const response = await fetch(`/api/target-repo-branches?target_repo=${encodeURIComponent(targetRepoRoot)}`);
        const payload = await response.json();
        if (!response.ok || !Array.isArray(payload.branches)) return;
        const branches = payload.branches.filter((branch) => branch && branch !== baseBranch);
        retrospectiveCompareOptions.innerHTML = branches.map((branch) => `<option value="${escapeHtml(branch)}"></option>`).join('');
        retrospectiveCompareBranchInput.value = currentValue;
      } catch (_error) {
        retrospectiveCompareOptions.innerHTML = '';
      }
    }

    function setRetrospectiveMode(mode, record = null) {
      const showChoice = mode === 'choice';
      if (record?.comparison_branch !== undefined) activeRetrospectiveComparisonBranch = record?.comparison_branch || '';
      retrospectiveChoiceShell.hidden = !showChoice;
      retrospectiveViewShell.hidden = showChoice;
      retrospectiveViewShell.style.display = showChoice ? 'none' : 'grid';
      retrospectiveContextRow.innerHTML = renderRetrospectiveContextTags(record);
      retrospectiveViewTitle.textContent = showChoice ? '' : translateTask('retrospectiveViewTitle');
    }

    function renderCardRuntime(item) {
      if (item.agent_status !== 'active') return '';
      const currentStateDurationMs = Number(item.current_state_duration_ms || 0);
      const activeSince = item.state_entered_at || '';
      return `<span class="card-runtime" ${buildDurationAttributes(0, activeSince)}>${formatElapsed(currentStateDurationMs)}</span>`;
    }

    function sortBoardItemsByUpdatedAt(items) {
      return [...(items || [])].sort((left, right) => Date.parse(right.updated_at || '') - Date.parse(left.updated_at || ''));
    }

    function groupFinalItemsByTargetBranch(items) {
      const grouped = new Map();
      sortBoardItemsByUpdatedAt(items).forEach((item) => {
        const branch = item.completed_group || item.base_branch || 'unknown';
        if (!grouped.has(branch)) grouped.set(branch, []);
        grouped.get(branch).push(item);
      });
      return [...grouped.entries()].sort((left, right) => {
        const leftUpdated = Date.parse(left[1][0]?.updated_at || '');
        const rightUpdated = Date.parse(right[1][0]?.updated_at || '');
        return rightUpdated - leftUpdated;
      });
    }

    function renderFinalProjectColumn(projectPath, items) {
      const sortedItems = sortBoardItemsByUpdatedAt(items);
      const projectLabel = sortedItems[0]?.target_repo_label || deriveRepoContext(projectPath).repoName || projectPath || stateLabel('done');
      const branchGroups = groupFinalItemsByTargetBranch(sortedItems);
      const repoTone = repoTagTone(projectPath);
      const columnStyle = `--repo-accent:${repoTone.text};--repo-accent-dark:${repoTone.darkAccent};--repo-accent-soft:${repoTone.background};`;
      return `
        <section class="column final-project-column" data-project-path="${escapeHtml(projectPath)}" style="${columnStyle}">
          <div class="final-project-heading">
            <h2 class="final-project-title" title="${escapeHtml(projectPath)}">${escapeHtml(projectLabel)}</h2>
          </div>
          <div class="final-project-branches">${branchGroups.map(([branch, branchItems], index) => `
            <section class="target-branch-group" data-branch="${escapeHtml(branch)}" data-expanded="${index === 0 ? 'true' : 'false'}">
              <div class="target-branch-label" title="${escapeHtml(branch)}" tabindex="0" role="button" aria-expanded="${index === 0 ? 'true' : 'false'}"><span class="target-branch-label-main"><span class="target-branch-title">${branchIconSvg('target-branch-icon')}<span class="target-branch-name">${escapeHtml(branch)}</span></span></span><span class="target-branch-label-side"><button type="button" class="target-branch-retrospective" data-target-repo="${escapeHtml(branchItems[0].target_repo_root || '')}" data-base-branch="${escapeHtml(branch)}">${escapeHtml(translateTask('retrospectiveCountLabel', { count: String(branchItems.length) }))}</button>${caretIconSvg('target-branch-caret')}</span></div>
              <div class="column-cards">${branchItems.map((item) => renderTaskCard(item, { compactFinal: true })).join('')}</div>
            </section>`).join('')}</div>
        </section>`;
    }

    function renderTaskCard(item, options = {}) {
      const { compactFinal = false } = options;
      const repoPath = normalizeRepoPath(item.target_repo_root);
      const repoTone = repoPath ? repoTagTone(repoPath) : null;
      const cardStyle = repoTone ? ` style="--card-accent:${repoTone.text};--card-accent-dark:${repoTone.darkAccent};"` : '';
      return `<article class="card"${cardStyle}><button class="card-button" data-task-id="${item.task_id}"><strong class="card-title">${item.title}</strong>${renderCardTags(item, { compactFinal })}${renderCardModelMeta(item)}</button></article>`;
    }

    function toggleFinalBranchGroup(branchLabel) {
      const group = branchLabel?.closest('.target-branch-group');
      if (!group) return;
      const expanded = group.dataset.expanded !== 'false';
      const nextExpanded = String(!expanded);
      group.dataset.expanded = nextExpanded;
      branchLabel.setAttribute('aria-expanded', nextExpanded);
    }

    function renderFinalBoard(columns) {
      const doneItems = sortBoardItemsByUpdatedAt(columns.flatMap((column) => column.items || []));
      if (!doneItems.length) {
        return renderBoardColumn({ state: 'done', items: [] });
      }
      const grouped = new Map();
      doneItems.forEach((item) => {
        const projectPath = normalizeRepoPath(item.target_repo_root) || item.target_repo_root || '.';
        if (!grouped.has(projectPath)) grouped.set(projectPath, []);
        grouped.get(projectPath).push(item);
      });
      const orderedGroups = [...grouped.entries()].sort((left, right) => {
        const leftUpdated = Date.parse(left[1][0]?.updated_at || '');
        const rightUpdated = Date.parse(right[1][0]?.updated_at || '');
        return rightUpdated - leftUpdated;
      });
      return orderedGroups.map(([projectPath, items]) => renderFinalProjectColumn(projectPath, items)).join('');
    }

    function renderBoardColumn(column) {
      const items = column.items || [];
      return `
        <section class="column" data-state="${column.state}">
          <h2>${stateLabel(column.state)}</h2>
          <div class="column-cards">${items.length ? items.map((item) => renderTaskCard(item)).join('') : `<div class="board-empty">${escapeHtml(translateTask('noItemsInColumn'))}</div>`}</div>
        </section>`;
    }

    function renderRequestDrafts() {
      if (!requestDraftList.length) {
        requestDraftsShell.hidden = true;
        requestDraftsGrid.innerHTML = '';
        setRequestDraftsStatus('');
        return;
      }
      requestDraftsShell.hidden = false;
      requestDraftsGrid.innerHTML = requestDraftList.map((draft) => {
        const repoPath = normalizeRepoPath(draft.target_repo || '');
        const repoLabel = deriveRepoContext(repoPath).repoName || repoPath || 'draft';
        const updatedLabel = translateRequest('draftsUpdated', { time: formatDateTime(draft.updated_at) });
        return `
          <article class="draft-card">
            <strong>${escapeHtml(draft.title || translateRequest('draftsTitle'))}</strong>
            <p>${escapeHtml(repoLabel)}${draft.base_branch ? ` · ${escapeHtml(draft.base_branch)}` : ''}</p>
            <div class="draft-card-meta">
              ${draft.has_transcript ? `<span class="diff-badge">${escapeHtml(translateRequest('draftTitle'))}</span>` : ''}
              ${draft.has_unsent_input ? `<span class="diff-badge">${escapeHtml(translateRequest('draftLiveSuffix').trim())}</span>` : ''}
              <span class="diff-badge">${escapeHtml(updatedLabel)}</span>
            </div>
            <div class="draft-card-actions">
              <button type="button" class="primary" data-request-draft-open="${escapeHtml(draft.draft_id)}">${escapeHtml(translateRequest('draftsContinue'))}</button>
              <button type="button" data-request-draft-delete="${escapeHtml(draft.draft_id)}">${escapeHtml(translateRequest('draftsDelete'))}</button>
            </div>
          </article>`;
      }).join('');
    }

    async function loadRequestDrafts() {
      const response = await fetch('/api/request-drafts');
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || 'Failed to load request drafts.');
      requestDraftList = Array.isArray(payload.items) ? payload.items : [];
      setRequestDraftsStatus('');
      renderRequestDrafts();
    }

    function renderCardModelMeta(item) {
      if (!item.active_model) return '';
      return `<div class="card-model"><strong>${escapeHtml(translateTask('currentStageModelUsed'))}</strong>${escapeHtml(item.active_model)}</div>`;
    }

    function normalizeAgentActivityStatus(status) {
      if (status === 'active' || status === 'idle' || status === 'waiting') return status;
      return '';
    }

    function formatRelativeTime(value) {
      if (!value) return translateTask('noHeartbeatYet');
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) return String(value);
      const deltaSeconds = Math.max(0, Math.round((Date.now() - parsed.getTime()) / 1000));
      if (deltaSeconds < 10) return translateSettings('justNow');
      const isKo = currentUiLanguage() === 'KO';
      if (deltaSeconds < 60) return isKo ? `${deltaSeconds}초 전` : `${deltaSeconds}s ago`;
      const deltaMinutes = Math.round(deltaSeconds / 60);
      if (deltaMinutes < 60) return isKo ? `${deltaMinutes}분 전` : `${deltaMinutes}m ago`;
      const deltaHours = Math.round(deltaMinutes / 60);
      if (deltaHours < 24) return isKo ? `${deltaHours}시간 전` : `${deltaHours}h ago`;
      const deltaDays = Math.round(deltaHours / 24);
      return isKo ? `${deltaDays}일 전` : `${deltaDays}d ago`;
    }

    function resolveAgentActivity({ status = '', owner = '', heartbeatAt = '', state = '' } = {}) {
      const explicitStatus = normalizeAgentActivityStatus(status);
      if (explicitStatus) return explicitStatus;
      const hasOwner = Boolean(owner);
      if (hasOwner && ['planning', 'implementing', 'reviewing'].includes(state)) return 'active';
      if (['planning', 'implementing', 'reviewing'].includes(state)) return 'waiting';
      return 'idle';
    }

    function activityLabel(status) {
      if (status === 'active') return currentUiLanguage() === 'KO' ? '에이전트 실행 중' : 'Agent active';
      if (status === 'waiting') return currentUiLanguage() === 'KO' ? '에이전트 대기 중' : 'Agent waiting';
      return currentUiLanguage() === 'KO' ? '에이전트 유휴' : 'Agent idle';
    }

    function buildActivityBadge(status, label = activityLabel(status)) {
      return `<span class="activity-badge" data-activity-state="${escapeHtml(status)}" aria-label="${escapeHtml(label)}"><span class="activity-dot" aria-hidden="true"></span><span class="sr-only">${escapeHtml(label)}</span></span>`;
    }

    function renderCardActivity(item) {
      const status = resolveAgentActivity({
        status: item.agent_status,
        owner: item.agent_owner,
        heartbeatAt: item.agent_heartbeat_at,
        state: item.state,
      });
      const owner = item.agent_owner ? `${currentUiLanguage() === 'KO' ? '소유자' : 'Owned by'} ${item.agent_owner}` : translateTask('noLeaseOwner');
      const meta = item.agent_heartbeat_at
        ? `${translateTask('heartbeat')} ${formatRelativeTime(item.agent_heartbeat_at)} · ${formatDateTime(item.agent_heartbeat_at)}`
        : (status === 'waiting' ? translateTask('waitingNoLeaseCard') : translateTask('noRuntimeLogForCard'));
      const tooltip = `${activityLabel(status)} - ${owner}. ${meta}`;
      return `<span class="card-activity" title="${escapeHtml(tooltip)}">${buildActivityBadge(status, tooltip)}</span>`;
    }

    function formatElapsed(milliseconds) {
      const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
      const hours = String(Math.floor(totalSeconds / 3600)).padStart(2, '0');
      const minutes = String(Math.floor((totalSeconds % 3600) / 60)).padStart(2, '0');
      const seconds = String(totalSeconds % 60).padStart(2, '0');
      return `${hours}:${minutes}:${seconds}`;
    }

    function buildDurationAttributes(baseDurationMs = 0, activeSince = '', prefix = '', suffix = '') {
      const normalizedBase = Number.isFinite(baseDurationMs) ? Math.max(0, Math.floor(baseDurationMs)) : 0;
      const activeAttr = activeSince ? ` data-active-since="${escapeHtml(activeSince)}"` : '';
      const prefixAttr = prefix ? ` data-duration-prefix="${escapeHtml(prefix)}"` : '';
      const suffixAttr = suffix ? ` data-duration-suffix="${escapeHtml(suffix)}"` : '';
      return `data-duration-target="true" data-duration-base-ms="${normalizedBase}"${activeAttr}${prefixAttr}${suffixAttr}`;
    }

    function stateLabel(state) {
      const labels = currentUiLanguage() === 'KO'
        ? {
            requests: '요구사항',
            planning: '계획 작성중',
            'plan-approving': '계획 자동 승인 판단중',
            'waiting-check-plans': '계획 승인 대기',
            todos: '구현 대기',
            implementing: '구현중',
            'waiting-reviews': '리뷰 대기중',
            reviewing: '리뷰중',
            'completed-reviews': '리뷰 완료',
            'human-verifying': '인간 리뷰중',
            done: '완료',
          }
        : {
            requests: 'Requirements',
            planning: 'Planning',
            'plan-approving': 'Plan gate decision',
            'waiting-check-plans': 'Plan approval',
            todos: 'Ready to implement',
            implementing: 'Implementing',
            'waiting-reviews': 'Awaiting review',
            reviewing: 'In review',
            'completed-reviews': 'Review complete',
            'human-verifying': 'Human review',
            done: 'Done',
          };
      return labels[state] || state;
    }

    function stageColor(state) {
      const palette = {
        requests: '#7b6b53',
        planning: '#7c4f2c',
        'plan-approving': '#8e5f2d',
        'waiting-check-plans': '#9a6c2f',
        todos: '#4f6877',
        implementing: '#217349',
        'waiting-reviews': '#2f6a7a',
        reviewing: '#3d5aa8',
        'completed-reviews': '#4f8a5b',
        'human-verifying': '#a55a2a',
        done: '#2f4f3f',
      };
      return palette[state] || '#7c4f2c';
    }

    function formatDateTime(value) {
      if (!value) return translateTask('now');
      const parsed = new Date(value);
      return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
    }

    function updateRunningClocks() {
      const now = Date.now();
      document.querySelectorAll('[data-duration-target="true"]').forEach((node) => {
        const baseDurationMs = Number(node.dataset.durationBaseMs || '0');
        const prefix = node.dataset.durationPrefix || '';
        const suffix = node.dataset.durationSuffix || '';
        const since = Date.parse(node.dataset.activeSince || '');
        const activeDurationMs = Number.isNaN(since) ? 0 : Math.max(0, now - since);
        node.textContent = `${prefix}${formatElapsed(baseDurationMs + activeDurationMs)}${suffix}`;
      });
    }

    function refreshRunningClocks() {
      if (runningTimerHandle) clearInterval(runningTimerHandle);
      updateRunningClocks();
      runningTimerHandle = window.setInterval(updateRunningClocks, 1000);
    }

    function clearTaskRefreshTimer() {
      if (activeTaskRefreshTimer) {
        clearTimeout(activeTaskRefreshTimer);
        activeTaskRefreshTimer = null;
      }
    }

    function clearReviewerQaRefreshInterval() {
      if (reviewerQaRefreshInterval) {
        clearInterval(reviewerQaRefreshInterval);
        reviewerQaRefreshInterval = null;
      }
    }

    function updateReviewerQaTranscriptPinnedToBottom() {
      const threshold = 24;
      reviewerQaTranscriptPinnedToBottom = (taskReviewerQaTranscript.scrollHeight - taskReviewerQaTranscript.scrollTop - taskReviewerQaTranscript.clientHeight) <= threshold;
    }

    function captureReviewerQaScrollState() {
      const maxScrollTop = Math.max(0, taskReviewerQaTranscript.scrollHeight - taskReviewerQaTranscript.clientHeight);
      return {
        pinnedToBottom: reviewerQaTranscriptPinnedToBottom,
        offsetFromBottom: maxScrollTop - taskReviewerQaTranscript.scrollTop,
      };
    }

    function restoreReviewerQaScrollState(state) {
      if (!state || state.pinnedToBottom) {
        scrollReviewerQaTranscriptToBottom();
        return;
      }
      const maxScrollTop = Math.max(0, taskReviewerQaTranscript.scrollHeight - taskReviewerQaTranscript.clientHeight);
      taskReviewerQaTranscript.scrollTop = Math.max(0, maxScrollTop - state.offsetFromBottom);
      updateReviewerQaTranscriptPinnedToBottom();
    }

    function scrollReviewerQaTranscriptToBottom() {
      taskReviewerQaTranscript.scrollTop = taskReviewerQaTranscript.scrollHeight;
      reviewerQaTranscriptPinnedToBottom = true;
    }

    function updateReviewerQaLiveRefresh() {
      clearReviewerQaRefreshInterval();
      const shouldWatchReviewerQa = !taskModal.hidden && Boolean(activeTaskId) && reviewerQaQuestionInFlight;
      if (!shouldWatchReviewerQa) return;
      reviewerQaRefreshInterval = window.setInterval(() => {
        if (taskModal.hidden || !activeTaskId || !reviewerQaQuestionInFlight) {
          clearReviewerQaRefreshInterval();
          return;
        }
        if (taskDetailStale) return;
        loadTaskDetail(activeTaskId, true, { softRefresh: true, reloadArtifact: false }).catch((error) => {
          taskModalError.hidden = false;
          taskModalError.textContent = error.message;
        });
      }, 1800);
    }

    function parseReviewerQaTranscript(source) {
      const normalized = (source || '').replace(/\r\n?/g, '\n').trim();
      if (!normalized) return [];
      const lines = normalized.split('\n');
      const entries = [];
      let currentEntry = null;
      for (const line of lines) {
        const headingMatch = /^##\s+(Question|Answer)\b.*$/i.exec(line.trim());
        if (headingMatch) {
          if (currentEntry) entries.push(currentEntry);
          currentEntry = { role: headingMatch[1].toLowerCase() === 'question' ? 'question' : 'answer', lines: [] };
          continue;
        }
        if (!currentEntry) {
          if (/^#\s+Reviewer Q&A\s*$/i.test(line.trim())) continue;
          currentEntry = { role: 'transcript', lines: [] };
        }
        currentEntry.lines.push(line);
      }
      if (currentEntry) entries.push(currentEntry);
      return entries
        .map((entry) => ({ ...entry, text: entry.lines.join('\n').trim() }))
        .filter((entry) => entry.text);
    }

    function buildReviewerQaEntries() {
      const entries = parseReviewerQaTranscript(reviewerQaSourceMarkdown);
      if (reviewerQaPendingQuestion) entries.push({ role: 'question', text: reviewerQaPendingQuestion, pending: reviewerQaQuestionInFlight });
      if (reviewerQaPendingAnswer || reviewerQaQuestionInFlight) {
        entries.push({
          role: 'answer',
          text: reviewerQaPendingAnswer || 'Reviewer is answering…',
          pending: reviewerQaQuestionInFlight,
        });
      }
      return entries;
    }

    function formatTranscriptLiveBadge(label) {
      return String(label || 'live').replace(/^[\s·•\-–—]+/, '').trim() || 'live';
    }

    function canShowReviewerQaRerequestAction() {
      const state = activeTaskDetail?.metadata?.state;
      return state === 'completed-reviews' || state === 'human-verifying';
    }

    function renderReviewerQaTranscript(entries) {
      if (!entries.length) {
        return '<p class="reviewer-qa-empty">No reviewer Q&amp;A recorded yet.</p>';
      }
      const lastAnsweredIndex = entries.reduce((latestIndex, entry, index) => {
        if (entry.role === 'answer' && !entry.pending) return index;
        return latestIndex;
      }, -1);
      const allowRerequest = canShowReviewerQaRerequestAction();
      const rerequestDisabled = taskDetailStale || reviewerQaRerequestInFlight;
      const rerequestActionStatus = reviewerQaRerequestInFlight
        ? '<div class="reviewer-qa-entry-action-status">재요청 중...</div>'
        : '';
      return entries.map((entry, index) => {
        const label = entry.role === 'question' ? 'You' : (entry.role === 'answer' ? 'Reviewer' : 'Transcript');
        const side = entry.role === 'question' ? 'current' : (entry.role === 'answer' ? 'other' : 'system');
        const liveBadge = entry.pending && entry.role === 'answer' ? `<span class="transcript-live-badge">${escapeHtml(formatTranscriptLiveBadge('live'))}</span>` : '';
        const rerequestAction = allowRerequest && entry.role === 'answer' && !entry.pending && lastAnsweredIndex === index
          ? `<div class="reviewer-qa-entry-actions"><button type="button" class="ghost-button reviewer-qa-rerequest" data-reviewer-qa-rerequest="true"${rerequestDisabled ? ' disabled' : ''}>재요청하기</button>${rerequestActionStatus}</div>`
          : '';
        return `<article class="reviewer-qa-entry" data-role="${escapeHtml(entry.role)}" data-side="${side}"${entry.pending ? ' data-pending="true"' : ''}><div class="reviewer-qa-shell"><div class="reviewer-qa-meta"><span class="reviewer-qa-role">${escapeHtml(label)}</span><div class="reviewer-qa-meta-badges">${liveBadge}</div></div><div class="reviewer-qa-bubble">${escapeHtml(entry.text)}</div>${rerequestAction}</div></article>`;
      }).join('');
    }

    function setTaskDetailStale(isStale, message = '') {
      taskDetailStale = isStale;
      if (isStale && message) {
        taskModalError.hidden = false;
        taskModalError.textContent = message;
      } else if (!isStale && !taskModalError.textContent) {
        taskModalError.hidden = true;
      }
      updatePlanActionState();
      updateHumanVerificationState();
      updateTaskDeleteState();
      updateReviewerQaPanel();
    }

    async function loadTargetRepoOptions() {
      const response = await fetch('/api/target-repos');
      if (!response.ok) return;
      const data = await response.json();
      targetRepoOptions.innerHTML = data.items.map((item) => `<option value="${item}"></option>`).join('');
      applyTargetRepoAutofill(data.items || []);
      targetRepoOptionsLoaded = true;
    }

    function updateBaseBranchHelp(message) {
      baseBranchHelp.textContent = message;
    }

    function replaceBaseBranchSuggestions(items) {
      baseBranchOptions.innerHTML = items.map((item) => `<option value="${escapeHtml(item)}"></option>`).join('');
    }

    function readLastTargetRepo() {
      try {
        const current = normalizeRepoPath(window.localStorage.getItem(lastTargetRepoStorageKey));
        if (current) return current;
        const legacy = normalizeRepoPath(window.localStorage.getItem(legacyLastTargetRepoStorageKey));
        if (!legacy) return '';
        window.localStorage.setItem(lastTargetRepoStorageKey, legacy);
        return legacy;
      } catch (_error) {
        return '';
      }
    }

    function persistLastTargetRepo(value) {
      const normalized = normalizeRepoPath(value);
      if (!normalized) return;
      try {
        window.localStorage.setItem(lastTargetRepoStorageKey, normalized);
      } catch (_error) {
      }
    }

    function serializeRequestDraftArtifactMarkdown() {
      const transcript = requestDraftEntries.filter((entry) => !entry.pending && (entry.role === 'user' || entry.role === 'assistant'));
      if (!transcript.length) return '';
      const sections = [];
      transcript.forEach((entry, index) => {
        sections.push(`## ${entry.role === 'user' ? translateRequest('draftUserLabel') : translateRequest('draftTitle')} ${index + 1}`);
        sections.push('');
        sections.push(entry.text || '');
        if (entry.role === 'assistant' && entry.field_updates && Object.keys(entry.field_updates).length) {
          sections.push('');
          sections.push(`### ${translateRequest('draftSuggestedUpdates')}`);
          sections.push('');
          Object.entries(entry.field_updates)
            .filter(([, value]) => value != null)
            .forEach(([fieldName, value]) => {
              sections.push(`- **${requestDraftFieldLabel(fieldName)}**: ${value === '' ? translateRequest('draftClearField') : value}`);
            });
        }
        sections.push('');
      });
      return sections.join('\n').trim();
    }

    function currentRequestComposerDraftPointer() {
      return {
        request_draft_id: requestDraftId || '',
      };
    }

    function currentRequestComposerDraftState() {
      syncRequestGoalField();
      return {
        request_draft_id: requestDraftId || '',
        title: requestTitleInput.value || '',
        target_repo: targetRepoInput.value || '',
        base_branch: baseBranchInput.value || '',
        background: requestBackgroundInput.value || '',
        goal: currentRequestGoalValue(),
        constraints: constraintsField.value || '',
        acceptance_criteria: acceptanceCriteriaField.value || '',
        scope: scopeField.value || '',
        out_of_scope: outOfScopeField.value || '',
        references: referencesField.value || '',
        plan_auto_approve: document.getElementById('plan_auto_approve').checked,
        active_tab: activeRequestComposerTab,
        request_upload_token: requestUploadToken || '',
        request_draft_input: requestDraftInput.value || '',
        request_draft_entries: requestDraftEntries.filter((entry) => !entry.pending).map((entry) => ({ role: entry.role, text: entry.text || '', field_updates: entry.field_updates || {} })),
        saved_at: Date.now(),
      };
    }

    function setRequestDraftsStatus(message = '', tone = 'neutral') {
      requestDraftsStatus.hidden = !message;
      requestDraftsStatus.dataset.tone = message ? tone : 'neutral';
      requestDraftsStatus.textContent = message || '';
    }

    function requestComposerDraftHasContent(state) {
      return Boolean(
        (state.title || '').trim() ||
        (state.background || '').trim() ||
        (state.goal || '').trim() ||
        (state.constraints || '').trim() ||
        (state.acceptance_criteria || '').trim() ||
        (state.scope || '').trim() ||
        (state.out_of_scope || '').trim() ||
        (state.references || '').trim() ||
        (state.request_draft_input || '').trim() ||
        (state.request_draft_entries || []).length
      );
    }

    function persistRequestComposerDraftPointer() {
      try {
        if (!requestDraftId) {
          window.localStorage.removeItem(requestComposerDraftStorageKey);
          return;
        }
        window.localStorage.setItem(requestComposerDraftStorageKey, JSON.stringify(currentRequestComposerDraftPointer()));
      } catch (_error) {
      }
    }

    function clearRequestComposerDraftState() {
      try {
        window.localStorage.removeItem(requestComposerDraftStorageKey);
      } catch (_error) {
      }
    }

    async function ensureRequestComposerDraft(options = {}) {
      if (requestDraftId) return requestDraftId;
      const { silent = false } = options;
      const state = currentRequestComposerDraftState();
      if (!requestComposerDraftHasContent(state)) return '';
      const response = await fetch('/api/request-drafts/state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(state),
      });
      const payload = await response.json();
      if (!response.ok) {
        if (!silent) throw new Error(payload.detail || 'Request draft creation failed.');
        return '';
      }
      requestDraftId = payload.draft_id || '';
      requestUploadToken = payload.request_upload_token || requestUploadToken;
      persistRequestComposerDraftPointer();
      return requestDraftId;
    }

    async function syncRequestComposerDraftState(options = {}) {
      const { immediate = false, silent = false } = options;
      if (requestDraftSyncTimer) {
        window.clearTimeout(requestDraftSyncTimer);
        requestDraftSyncTimer = null;
      }
      const run = async () => {
        const state = currentRequestComposerDraftState();
        if (!requestComposerDraftHasContent(state)) {
          if (requestDraftId) {
            await deleteRequestComposerDraftState({ preserveFormState: true, silent: true });
            await loadRequestDrafts().catch(() => {});
          }
          else clearRequestComposerDraftState();
          return;
        }
        const draftId = await ensureRequestComposerDraft({ silent });
        if (!draftId) return;
        const response = await fetch(`/api/request-drafts/${encodeURIComponent(draftId)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(currentRequestComposerDraftState()),
        });
        const payload = await response.json();
        if (!response.ok) {
          if (!silent) throw new Error(payload.detail || 'Request draft save failed.');
          return;
        }
        requestDraftId = payload.draft_id || requestDraftId;
        requestUploadToken = payload.request_upload_token || requestUploadToken;
        persistRequestComposerDraftPointer();
        await loadRequestDrafts().catch(() => {});
      };
      if (immediate) return run();
      requestDraftSyncTimer = window.setTimeout(() => {
        requestDraftSyncTimer = null;
        void run();
      }, requestComposerDraftSyncDelayMs);
    }

    async function deleteRequestComposerDraftState(options = {}) {
      const { preserveFormState = false, silent = false } = options;
      if (requestDraftSyncTimer) {
        window.clearTimeout(requestDraftSyncTimer);
        requestDraftSyncTimer = null;
      }
      const draftId = requestDraftId;
      requestDraftId = '';
      if (!preserveFormState) requestDraftEntries = [];
      clearRequestComposerDraftState();
      if (!draftId) return;
      try {
        await fetch(`/api/request-drafts/${encodeURIComponent(draftId)}`, { method: 'DELETE' });
      } catch (error) {
        if (!silent) throw error;
      }
    }

    function applyRequestComposerDraftState(saved) {
      requestForm.reset();
      requestDraftId = saved.draft_id || saved.request_draft_id || '';
      requestTitleInput.value = saved.title || '';
      targetRepoInput.value = saved.target_repo || defaultTargetRepo;
      targetRepoInput.dataset.autofilled = 'false';
      baseBranchInput.value = saved.base_branch || defaultBaseBranch;
      baseBranchInput.dataset.autofilled = 'false';
      requestBackgroundInput.value = saved.background || '';
      setRequestGoalEditorContent(saved.goal || '', { initialize: false });
      constraintsField.value = saved.constraints || '';
      acceptanceCriteriaField.value = saved.acceptance_criteria || '';
      scopeField.value = saved.scope || '';
      outOfScopeField.value = saved.out_of_scope || '';
      referencesField.value = saved.references || '';
      document.getElementById('plan_auto_approve').checked = Boolean(saved.plan_auto_approve);
      requestUploadToken = saved.request_upload_token || generateRequestUploadToken();
      requestDraftInput.value = saved.request_draft_input || '';
      requestDraftEntries = Array.isArray(saved.transcript)
        ? saved.transcript
            .filter((entry) => entry && (entry.role === 'user' || entry.role === 'assistant'))
            .map((entry) => ({ role: entry.role, text: entry.content || '', field_updates: entry.field_updates || {} }))
        : [];
      requestDraftPendingMessage = '';
      requestDraftMessageInFlight = false;
      requestDraftAttachmentInFlight = false;
      requestDraftAttachmentStatusKey = '';
      requestDraftAttachmentStatusMessage = '';
      requestDraftAttachmentStatusTone = 'neutral';
      requestDraftAttachmentStatusVars = {};
      requestDraftDropDepth = 0;
      requestDraftLastRenderedSignature = '';
      requestDraftTranscriptPinnedToBottom = true;
      setRequestComposerTab(saved.active_tab === 'fields' ? 'fields' : 'assistant');
      updateRequestDraftPanel();
      persistRequestComposerDraftPointer();
    }

    async function restoreRequestComposerDraftState() {
      try {
        const raw = window.localStorage.getItem(requestComposerDraftStorageKey);
        if (!raw) return false;
        const saved = JSON.parse(raw);
        const draftId = (saved?.request_draft_id || '').trim();
        if (!draftId) {
          clearRequestComposerDraftState();
          setRequestDraftsStatus('');
          return false;
        }
        const response = await fetch(`/api/request-drafts/${encodeURIComponent(draftId)}`);
        const payload = await response.json();
        if (!response.ok) {
          clearRequestComposerDraftState();
          setRequestDraftsStatus(payload.detail || 'Failed to load request draft.', 'error');
          await loadRequestDrafts().catch(() => {});
          return false;
        }
        setRequestDraftsStatus('');
        applyRequestComposerDraftState(payload);
        return true;
      } catch (_error) {
        clearRequestComposerDraftState();
        return false;
      }
    }

    async function openRequestDraftFromList(draftId) {
      if (!draftId) return;
      clearMessages();
      applyRequestTranslations();
      try {
        const response = await fetch(`/api/request-drafts/${encodeURIComponent(draftId)}`);
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || 'Failed to load request draft.');
        setRequestDraftsStatus('');
        clearRequestComposerDraftState();
        window.localStorage.setItem(requestComposerDraftStorageKey, JSON.stringify({ request_draft_id: draftId }));
        applyRequestComposerDraftState(payload);
        setModalOpen(true);
        await loadTargetRepoBranches();
      } catch (error) {
        setRequestDraftsStatus(error.message, 'error');
        await loadRequestDrafts().catch(() => {});
      }
    }

    async function deleteRequestDraftFromList(draftId) {
      if (!draftId) return;
      try {
        const response = await fetch(`/api/request-drafts/${encodeURIComponent(draftId)}`, { method: 'DELETE' });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateRequest('draftsDeleteError'));
        setRequestDraftsStatus('');
        if (requestDraftId === draftId) {
          requestDraftId = '';
          clearRequestComposerDraftState();
        }
        await loadRequestDrafts();
      } catch (error) {
        setRequestDraftsStatus(error.message, 'error');
      }
    }

