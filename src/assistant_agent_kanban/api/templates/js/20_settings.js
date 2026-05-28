
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
      taskTabInspector.textContent = translateTask('tabInspector');
      taskTabLogs.textContent = translateTask('tabLogs');
      taskTabEditor.textContent = translateTask('tabViewer');
      taskTabChangedFiles.textContent = translateTask('tabChangedFiles');
      taskTabQaChecklist.textContent = translateHumanReview('qaChecklistTab');
      setTaskText('task-inspector-title', 'taskInspector');
      setTaskText('refresh-task-inspection', 'inspectorRefresh');
      setTaskText('task-inspector-faq-title', 'inspectorFaqTitle');
      taskInspectorInput.placeholder = translateTask('inspectorInputPlaceholder');
      askTaskInspectorButton.textContent = translateTask('inspectorAsk');
      if (activeTaskInspection && typeof renderTaskInspection === 'function') renderTaskInspection(activeTaskInspection);
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
      setSettingsText('settings-tab-git', 'settingsTabGit');
      setSettingsText('settings-tab-slack', 'settingsTabSlack');
      setSettingsText('settings-tab-slack-channel', 'settingsTabSlackChannel');
      setSettingsText('settings-tab-roles', 'settingsTabRoles');
      setSettingsText('settings-tab-repositories', 'settingsTabRepositories');
      setSettingsText('settings-tab-users', 'settingsTabUsers');
      if (logoutButton) logoutButton.textContent = translateSettings('logout');
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
      setSettingsText('settings-git-heading', 'gitHeading');
      setSettingsText('settings-git-description', 'gitDescription');
      setSettingsText('settings-git-token-username-title', 'gitTokenUsernameTitle');
      setSettingsText('settings-git-token-username-description', 'gitTokenUsernameDescription');
      setSettingsHtml('settings-git-token-username-note', 'gitTokenUsernameNote');
      setSettingsText('settings-git-token-title', 'gitTokenTitle');
      setSettingsText('settings-git-token-description', 'gitTokenDescription');
      setSettingsText('settings-git-unlock-key-title', 'gitUnlockKeyTitle');
      setSettingsText('settings-git-unlock-key-description', 'gitUnlockKeyDescription');
      updateGitUnlockKeyStatus();
      setSettingsText('settings-repositories-heading', 'repositoriesHeading');
      setSettingsText('settings-repositories-description', 'repositoriesDescription');
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
      setSettingsText('account-modal-title', 'accountTitle');
      setSettingsText('account-modal-description', 'accountDescription');
      setSettingsText('account-password-heading', 'passwordHeading');
      setSettingsText('account-password-description', 'passwordDescription');
      setSettingsText('settings-current-password-title', 'currentPasswordTitle');
      setSettingsText('settings-change-password-title', 'changePasswordTitle');
      setSettingsText('settings-confirm-password-title', 'confirmPasswordTitle');
      if (changePasswordButton) changePasswordButton.textContent = translateSettings('updatePassword');
      setSettingsText('settings-users-heading', 'usersHeading');
      setSettingsText('settings-users-description', 'usersDescription');
      setSettingsText('settings-remote-usage-title', 'remoteUsageTitle');
      setSettingsText('settings-remote-usage-description', 'remoteUsageDescription');
      setSettingsText('settings-new-user-heading', 'newUserHeading');
      setSettingsText('settings-new-user-title', 'newUserTitle');
      setSettingsText('settings-new-user-description', 'newUserDescription');
      setSettingsText('settings-new-user-password-title', 'newUserPasswordTitle');
      setSettingsText('settings-new-user-admin-label', 'newUserAdminLabel');
      setSettingsText('settings-new-user-admin-description', 'newUserAdminDescription');
      setSettingsText('settings-user-list-title', 'userListTitle');
      if (createUserButton) createUserButton.textContent = translateSettings('createUser');
      updateUserManagementModeControls();
      if (btnBrowseRepoRoot) btnBrowseRepoRoot.textContent = translateSettings('dirPickerOpen');
      setSettingsText('directory-picker-title', 'dirPickerTitle');
      setSettingsText('directory-picker-description', 'dirPickerDesc');
      if (btnDirectoryPickerSelect) btnDirectoryPickerSelect.textContent = translateSettings('dirPickerSelect');
      if (btnDirectoryPickerClose) btnDirectoryPickerClose.textContent = translateSettings('dirPickerClose');
      setSettingsText('settings-slack-title', 'slackTitle');
      setSettingsText('settings-slack-description', canEditCommonSettings() ? 'slackChannelAdminDescription' : 'slackUserDescription');
      setSettingsText('settings-slack-basics-title', 'slackBasicsTitle');
      setSettingsText('settings-slack-auth-title', 'slackConnectionTitle');
      setSettingsText('settings-slack-auth-description', 'slackConnectionDescription');
      setSettingsText('settings-slack-enabled-label', 'slackEnabledLabel');
      setSettingsText('settings-slack-socket-mode-label', 'slackSocketModeLabel');
      setSettingsText('settings-slack-mention-label', 'slackMentionLabel');
      setSettingsHtml('settings-slack-note', 'slackNote');
      setSettingsText('settings-slack-save-note-title', 'slackSaveNoteTitle');
      setSettingsHtml('settings-slack-save-note', 'slackSaveNote');
      setSettingsText('settings-slack-bot-token-label', 'slackBotTokenLabel');
      setSettingsText('settings-slack-app-token-label', 'slackAppTokenLabel');
      setSettingsText('settings-slack-channel-label', 'slackChannelLabel');
      const slackBotDisplayName = lastSettingsPayload?.slack_bot_name || translateSettings('slackBotFallbackName');
      const highlightedBot = `<strong class="slack-bot-highlight">${escapeHtml(slackBotDisplayName)}</strong>`;
      setSettingsHtml(
        'settings-slack-notice-banner',
        'slackChannelNoticeBanner',
        { bot: highlightedBot }
      );
      setSettingsHtml(
        'settings-slack-basics-description',
        canEditCommonSettings() ? 'slackChannelAdminBasicsDescription' : 'slackBasicsDescription',
        { bot: highlightedBot }
      );
      setSettingsHtml(
        'settings-slack-channel-description',
        canEditCommonSettings() ? 'slackChannelDescription' : 'slackUserChannelDescription',
        { bot: highlightedBot },
      );
      setSettingsText('settings-slack-effective-channel-label', 'slackEffectiveChannelLabel');
      setSettingsHtml('settings-slack-effective-channel-help', 'slackEffectiveChannelHelp');
      setSettingsHtml(
        'settings-slack-channel-note',
        canEditCommonSettings() ? 'slackChannelNote' : 'slackUserChannelNote',
        { bot: highlightedBot },
      );
      setSettingsText('settings-slack-advanced-title', 'slackAdvancedTitle');
      setSettingsText('settings-slack-advanced-description', 'slackAdvancedDescription');
      setSettingsText('settings-slack-advanced-note', 'slackAdvancedNote');
      setSettingsText('settings-slack-test-description', 'slackTestDescription');
      setSettingsText('settings-slack-bot-name-label', 'slackBotNameLabel');
      setSettingsHtml('settings-slack-bot-name-note', 'slackBotNameNote');
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
      saveSettingsButtons.forEach((button) => {
        button.textContent = translateSettings('saveSettings');
      });
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
      updateSlackPermissionControls();
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
      submitButton.textContent = activeRequestComposerTab === 'assistant' ? translateRequest('reviewDetails') : translateRequest('submit');
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
        { value: 'claude', label: 'Claude Code' },
        { value: 'codex', label: 'Codex CLI' },
        { value: 'antigravity', label: 'Antigravity CLI' },
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
      const available = defaults
        .filter(({ value }) => availabilityByBackend[value] && availabilityByBackend[value].available)
        .map(({ value }) => ({ value, label: labelByValue.get(value) || value }));
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

    function isRepoDiscoveryReadonly() {
      if (lastSettingsPayload && Object.prototype.hasOwnProperty.call(lastSettingsPayload, 'repo_discovery_readonly')) {
        return Boolean(lastSettingsPayload.repo_discovery_readonly);
      }
      const user = currentAuthUser();
      return Boolean(currentAuthPayload?.enabled && user && !user.is_admin);
    }

    function canEditCommonSettings() {
      if (lastSettingsPayload && Object.prototype.hasOwnProperty.call(lastSettingsPayload, 'can_edit_common_settings')) {
        return Boolean(lastSettingsPayload.can_edit_common_settings);
      }
      const user = currentAuthUser();
      return !currentAuthPayload?.enabled || !user || Boolean(user.is_admin);
    }

    function updateSettingsPermissionControls() {
      const repoDiscoveryReadonly = isRepoDiscoveryReadonly();
      if (repoDiscoveryRootInput) {
        repoDiscoveryRootInput.disabled = repoDiscoveryReadonly;
        repoDiscoveryRootInput.setAttribute('aria-readonly', String(repoDiscoveryReadonly));
      }
      if (repoDiscoveryMaxDepthInput) {
        repoDiscoveryMaxDepthInput.disabled = repoDiscoveryReadonly;
        repoDiscoveryMaxDepthInput.setAttribute('aria-readonly', String(repoDiscoveryReadonly));
      }
      if (btnBrowseRepoRoot) btnBrowseRepoRoot.disabled = repoDiscoveryReadonly;
      [repoDiscoveryRootInput, repoDiscoveryMaxDepthInput].forEach((input) => {
        const card = input?.closest?.('.settings-card');
        if (card) card.classList.toggle('settings-card-readonly', repoDiscoveryReadonly);
      });
      updateSlackPermissionControls();
    }

    function updateSlackPermissionControls() {
      const canEditSlackConnection = canEditCommonSettings();
      const adminSection = document.getElementById('settings-slack-admin-section');
      if (adminSection) {
        adminSection.hidden = !canEditSlackConnection;
      }
      if (settingsSlackTab) settingsSlackTab.hidden = !canEditSlackConnection;
      if (!canEditSlackConnection && settingsSlackPanel && !settingsSlackPanel.hidden) {
        setSettingsTab('slack-channel', { guardUnsaved: false });
      }
      [slackEnabledInput, slackSocketModeEnabledInput, slackAppMentionEnabledInput, slackBotNameInput, slackBotTokenInput, slackAppTokenInput].forEach((input) => {
        if (input) input.disabled = !canEditSlackConnection;
      });
      if (clearSlackBotTokenButton) clearSlackBotTokenButton.hidden = !canEditSlackConnection;
      if (clearSlackAppTokenButton) clearSlackAppTokenButton.hidden = !canEditSlackConnection;
    }

    function captureSettingsState() {
      return {
        language: runtimeLanguageInput.value || 'EN',
        theme: runtimeThemeInput.value || 'light',
        coding_assistant: runtimeCodingAssistantInput.value || 'opencode',
        slack_enabled: slackEnabledInput.checked,
        slack_socket_mode_enabled: slackSocketModeEnabledInput.checked,
        slack_app_mention_enabled: slackAppMentionEnabledInput.checked,
        slack_bot_name: slackBotNameInput?.value || '',
        slack_bot_token: slackBotTokenInput.value || '',
        slack_app_token: slackAppTokenInput.value || '',
        slack_bot_token_clear_requested: slackBotTokenClearRequested,
        slack_app_token_clear_requested: slackAppTokenClearRequested,
        slack_default_channel: slackDefaultChannelInput.value || '',
        git_token_username: gitTokenUsernameInput?.value || '',
        git_token: gitTokenInput?.value || '',
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

    function updateGitTokenStatus(data) {
      if (!gitTokenStatus) return;
      if (gitTokenInput?.value) {
        gitTokenStatus.textContent = translateSettings('gitTokenStatusWillReplace');
        return;
      }
      if (data?.git_token_configured && data?.git_token_masked) {
        gitTokenStatus.textContent = translateSettings('gitTokenStatusConfigured', { masked: data.git_token_masked });
        return;
      }
      gitTokenStatus.textContent = translateSettings('gitTokenStatusNotConfigured');
    }

    function updateGitUnlockKeyStatus() {
      if (!gitTokenUnlockKeyNote) return;
      const inlineValue = (gitTokenUnlockKeyInput?.value || '').trim();
      if (inlineValue) {
        gitTokenUnlockKeyNote.textContent = translateSettings('gitUnlockKeyWillSaveLocal', { fingerprint: '...' });
        updateGitUnlockKeyFingerprint(inlineValue, 'gitUnlockKeyWillSaveLocal', () => (gitTokenUnlockKeyInput?.value || '').trim() === inlineValue);
        return;
      }
      const localValue = readGitTokenUnlockLocal();
      if (localValue) {
        gitTokenUnlockKeyNote.textContent = translateSettings('gitUnlockKeySavedLocal', { fingerprint: '...' });
        updateGitUnlockKeyFingerprint(localValue, 'gitUnlockKeySavedLocal', () => !(gitTokenUnlockKeyInput?.value || '').trim() && readGitTokenUnlockLocal() === localValue);
        return;
      }
      gitTokenUnlockKeyNote.textContent = translateSettings('gitUnlockKeyNote');
    }

    function updateGitUnlockKeyFingerprint(value, translationKey, stillCurrent) {
      gitUnlockKeyFingerprint(value).then((fingerprint) => {
        if (!gitTokenUnlockKeyNote || !stillCurrent()) return;
        gitTokenUnlockKeyNote.textContent = translateSettings(translationKey, { fingerprint });
      }).catch(() => {});
    }

    async function gitUnlockKeyFingerprint(value) {
      if (!crypto?.subtle) return 'unavailable';
      const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
      return Array.from(new Uint8Array(digest).slice(0, 6))
        .map((byte) => byte.toString(16).padStart(2, '0'))
        .join('');
    }

    function gitTokenAad() {
      const user = currentAuthUser();
      return JSON.stringify({
        purpose: 'assistant-agent-kanban.git-token',
        version: 1,
        user_id: user?.user_id || '',
      });
    }

    function gitTokenUnlockLocalStorageKey() {
      const userId = currentAuthUser()?.user_id || 'local';
      return `assistant-agent-kanban.git-token-unlock.${userId}`;
    }

    function readGitTokenUnlockLocal() {
      try {
        return localStorage.getItem(gitTokenUnlockLocalStorageKey()) || '';
      } catch {
        return '';
      }
    }

    function writeGitTokenUnlockLocal(value) {
      const text = (value || '').trim();
      if (!text) return;
      try {
        localStorage.setItem(gitTokenUnlockLocalStorageKey(), text);
      } catch {
        // Local storage can be unavailable in restricted browser contexts.
      }
      updateGitUnlockKeyStatus();
    }

    function clearGitTokenUnlockLocal() {
      try {
        localStorage.removeItem(gitTokenUnlockLocalStorageKey());
      } catch {
        // Local storage can be unavailable in restricted browser contexts.
      }
      updateGitUnlockKeyStatus();
    }

    function maskGitTokenForDisplay(value) {
      const text = value || '';
      if (!text) return '';
      if (text.length <= 4) return '•'.repeat(text.length);
      return `${'•'.repeat(text.length - 4)}${text.slice(-4)}`;
    }

    function base64FromBytes(bytes) {
      let binary = '';
      bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
      return btoa(binary);
    }

    function cryptoBytes(length) {
      const bytes = new Uint8Array(length);
      crypto.getRandomValues(bytes);
      return bytes;
    }

    async function encryptGitTokenForStorage(token, unlockKey) {
      if (!crypto?.subtle || !crypto?.getRandomValues) {
        throw new Error(translateSettings('gitCryptoUnavailable'));
      }
      const trimmedToken = (token || '').trim();
      const trimmedUnlockKey = (unlockKey || '').trim();
      if (!trimmedToken) throw new Error(translateSettings('gitTokenRequiredForEncryption'));
      if (!trimmedUnlockKey) throw new Error(translateSettings('gitUnlockKeyRequired'));
      const encoder = new TextEncoder();
      const salt = cryptoBytes(16);
      const nonce = cryptoBytes(12);
      const aad = gitTokenAad();
      const baseKey = await crypto.subtle.importKey(
        'raw',
        encoder.encode(trimmedUnlockKey),
        'PBKDF2',
        false,
        ['deriveKey'],
      );
      const key = await crypto.subtle.deriveKey(
        {
          name: 'PBKDF2',
          hash: 'SHA-256',
          salt,
          iterations: 600000,
        },
        baseKey,
        { name: 'AES-GCM', length: 256 },
        false,
        ['encrypt'],
      );
      const ciphertext = await crypto.subtle.encrypt(
        { name: 'AES-GCM', iv: nonce, additionalData: encoder.encode(aad) },
        key,
        encoder.encode(trimmedToken),
      );
      return {
        version: 1,
        algorithm: 'AES-256-GCM',
        kdf: 'PBKDF2-SHA256',
        kdf_iterations: 600000,
        salt: base64FromBytes(salt),
        nonce: base64FromBytes(nonce),
        ciphertext: base64FromBytes(new Uint8Array(ciphertext)),
        aad,
      };
    }

    function resolveGitUnlockKeyForOperation() {
      const inlineValue = (gitTokenUnlockKeyInput?.value || '').trim();
      if (inlineValue) return inlineValue;
      const localValue = readGitTokenUnlockLocal();
      if (localValue) return localValue;
      const promptedValue = (window.prompt(translateSettings('gitUnlockPrompt')) || '').trim();
      if (promptedValue) writeGitTokenUnlockLocal(promptedValue);
      return promptedValue;
    }

    function gitUnlockBodyForOperation(extra = {}) {
      if (!currentAuthPayload?.enabled || !currentAuthPayload?.authenticated) return { ...extra };
      const unlockKey = resolveGitUnlockKeyForOperation();
      if (!unlockKey) return null;
      return { ...extra, git_token_unlock_key: unlockKey };
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

    function buildSlackSettingsPayload({ includeConnection = canEditCommonSettings(), includeChannel = true } = {}) {
      const payload = {};
      if (includeChannel) {
        payload.slack_default_channel = slackDefaultChannelInput.value;
      }
      if (includeConnection && canEditCommonSettings()) {
        payload.slack_enabled = slackEnabledInput.checked;
        payload.slack_socket_mode_enabled = slackSocketModeEnabledInput.checked;
        payload.slack_app_mention_enabled = slackAppMentionEnabledInput.checked;
        payload.slack_bot_name = slackBotNameInput?.value || '';
      }
      if (includeConnection && canEditCommonSettings()) {
        if (slackBotTokenClearRequested || slackBotTokenInput.value) {
          payload.slack_bot_token = slackBotTokenClearRequested ? '' : slackBotTokenInput.value;
        }
        if (slackAppTokenClearRequested || slackAppTokenInput.value) {
          payload.slack_app_token = slackAppTokenClearRequested ? '' : slackAppTokenInput.value;
        }
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
      if (slackBotNameInput) slackBotNameInput.value = data.slack_bot_name || '';
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
      if (slackBotNameInput) slackBotNameInput.value = state.slack_bot_name || '';
      slackBotTokenInput.value = state.slack_bot_token || '';
      slackAppTokenInput.value = state.slack_app_token || '';
      slackBotTokenClearRequested = Boolean(state.slack_bot_token_clear_requested);
      slackAppTokenClearRequested = Boolean(state.slack_app_token_clear_requested);
      slackDefaultChannelInput.value = state.slack_default_channel || '';
      if (gitTokenUsernameInput) gitTokenUsernameInput.value = state.git_token_username || '';
      if (gitTokenInput) gitTokenInput.value = state.git_token || '';
      if (gitTokenUnlockKeyInput) gitTokenUnlockKeyInput.value = '';
      updateGitTokenStatus(lastSettingsPayload);
      updateGitUnlockKeyStatus();
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
      discardUserManagementDraft();
    }

    function applyRuntimeTheme(theme) {
      body.dataset.theme = theme === 'dark' ? 'dark' : 'light';
      const isDark = theme === 'dark';
      document.querySelectorAll('.toastui-editor-defaultUI, .toastui-editor-contents, .toastui-editor-main, .toastui-editor-md-container, .toastui-editor-ww-container').forEach((el) => {
        el.classList.toggle('toastui-editor-dark', isDark);
      });
    }

    function closeSettingsModal({ restore = false, force = false } = {}) {
      if (!force && !confirmUnsavedSettingsBeforeClose()) return;
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

    function currentAuthUser() {
      if (currentAuthPayload) return currentAuthPayload.user || null;
      return lastSettingsPayload?.user || null;
    }

    function isRemoteUsageActive() {
      return Boolean(currentAuthPayload?.enabled || currentAuthUser());
    }

    function isUserManagementActivationMode() {
      return Boolean(currentAuthPayload && !isRemoteUsageActive() && remoteUsageSetupRequested);
    }

    function canChangeOwnPassword() {
      return Boolean(currentAuthPayload?.enabled && currentAuthUser());
    }

    function authAvatarGradient(username) {
      const avatarGradients = [
        'linear-gradient(135deg, #6366f1, #4f46e5)',
        'linear-gradient(135deg, #ec4899, #be185d)',
        'linear-gradient(135deg, #10b981, #047857)',
        'linear-gradient(135deg, #f59e0b, #d97706)',
        'linear-gradient(135deg, #3b82f6, #1d4ed8)',
        'linear-gradient(135deg, #8b5cf6, #6d28d9)'
      ];
      const charCodeSum = (username || '').split('').reduce((acc, char) => acc + char.charCodeAt(0), 0);
      return avatarGradients[charCodeSum % avatarGradients.length];
    }

    function hasUnsavedUserManagementChanges() {
      return Boolean(
        (!isRemoteUsageActive() && remoteUsageSetupRequested)
        || (newUserUsernameInput?.value || '').trim()
        || newUserPasswordInput?.value
      );
    }

    function isOnboardingActive() {
      const onboardingOverlay = document.getElementById('onboarding-overlay');
      return Boolean(onboardingOverlay && !onboardingOverlay.hidden);
    }

    function discardUserManagementDraft() {
      if (!isRemoteUsageActive()) {
        remoteUsageSetupRequested = false;
      }
      if (newUserUsernameInput) newUserUsernameInput.value = '';
      if (newUserPasswordInput) newUserPasswordInput.value = '';
      if (newUserIsAdminInput) newUserIsAdminInput.checked = false;
      setCreateUserStatus('');
      updateUserManagementModeControls();
      if (!isRemoteUsageActive()) renderUsers([]);
    }

    function resetAccountPasswordForm() {
      if (currentUserPasswordInput) {
        currentUserPasswordInput.value = '';
        currentUserPasswordInput.type = 'password';
      }
      if (newUserPasswordChangeInput) {
        newUserPasswordChangeInput.value = '';
        newUserPasswordChangeInput.type = 'password';
      }
      if (confirmUserPasswordChangeInput) {
        confirmUserPasswordChangeInput.value = '';
        confirmUserPasswordChangeInput.type = 'password';
      }
      document.querySelectorAll('.password-visibility-toggle').forEach((button) => {
        const eyeOpen = button.querySelector('.eye-open-icon');
        const eyeClosed = button.querySelector('.eye-closed-icon');
        if (eyeOpen && eyeClosed) {
          eyeOpen.hidden = false;
          eyeClosed.hidden = true;
        }
      });
      setPasswordChangeStatus('');
    }

    function canManageUsers() {
      const user = currentAuthUser();
      return Boolean(currentAuthPayload && !isRemoteUsageActive()) || Boolean(user?.is_admin);
    }

    function updateUserManagementModeControls() {
      const activationMode = isUserManagementActivationMode();
      const remoteUsageActive = isRemoteUsageActive();
      const setupRequested = !remoteUsageActive && remoteUsageSetupRequested;
      const canManageUserAccounts = canManageUsers();
      setSettingsText('settings-users-heading', activationMode ? 'userManagementActivationHeading' : 'usersHeading');
      setSettingsText('settings-users-description', activationMode ? 'userManagementActivationDescription' : 'usersDescription');
      setSettingsText('settings-remote-usage-title', 'remoteUsageTitle');
      setSettingsText('settings-remote-usage-description', 'remoteUsageDescription');
      setSettingsText('settings-new-user-heading', activationMode ? 'userManagementActivationCreateHeading' : 'newUserHeading');
      setSettingsText('settings-new-user-title', activationMode ? 'userManagementActivationAdminTitle' : 'newUserTitle');
      setSettingsText('settings-new-user-description', activationMode ? 'userManagementActivationAdminDescription' : 'newUserDescription');
      setSettingsText('settings-user-list-title', 'userListTitle');
      if (remoteUsageStatus) {
        const statusKey = remoteUsageActive ? 'remoteUsageEnabledStatus' : (setupRequested ? 'remoteUsageSetupStatus' : 'remoteUsageDisabledStatus');
        remoteUsageStatus.textContent = setupRequested && !remoteUsageActive
          ? `${translateSettings(statusKey)} · ${translateSettings('remoteUsageDisabledHint')}`
          : translateSettings(statusKey);
        remoteUsageStatus.dataset.tone = remoteUsageActive ? 'success' : (setupRequested ? 'warning' : 'neutral');
      }
      if (remoteUsageEnabledInput) {
        remoteUsageEnabledInput.checked = remoteUsageActive || setupRequested;
      }
      if (remoteUsageCard) remoteUsageCard.hidden = !canManageUserAccounts && remoteUsageActive;
      if (userCreateCard) userCreateCard.hidden = (!remoteUsageActive && !setupRequested) || (remoteUsageActive && !canManageUserAccounts);
      if (userListCard) userListCard.hidden = !remoteUsageActive || !canManageUserAccounts;
      if (createUserButton) createUserButton.textContent = translateSettings(activationMode ? 'activateUserManagement' : 'createUser');
      if (newUserIsAdminInput) {
        newUserIsAdminInput.checked = activationMode || newUserIsAdminInput.checked;
        newUserIsAdminInput.disabled = activationMode;
      }
      const adminToggle = document.getElementById('settings-new-user-admin-toggle');
      if (adminToggle) adminToggle.hidden = activationMode;
    }

    function updateAuthControls(data) {
      if (data) currentAuthPayload = data;
      if (isRemoteUsageActive()) {
        remoteUsageSetupRequested = false;
      }
      const user = currentAuthUser();
      const enabled = Boolean(currentAuthPayload?.enabled || user);
      const authenticated = Boolean(user && enabled);
      if (authUserLabel) {
        authUserLabel.hidden = !authenticated;
        if (authenticated) {
          const username = user.username || '';
          const initial = username.charAt(0).toUpperCase();
          const bgGradient = authAvatarGradient(username);
          const roleClass = user.is_admin ? 'badge-admin' : 'badge-member';
          const roleLabel = user.is_admin ? translateSettings('userRoleAdmin') : translateSettings('userRoleMember');

          authUserLabel.setAttribute('aria-label', translateSettings('accountOpenLabel'));
          authUserLabel.title = translateSettings('accountOpenLabel');
          authUserLabel.innerHTML = `
            <div class="header-user-avatar" style="background: ${bgGradient};" title="${escapeHtml(roleLabel)}">${escapeHtml(initial)}</div>
            <span class="header-username">${escapeHtml(username)}</span>
            <span class="header-user-badge ${roleClass}">${escapeHtml(roleLabel)}</span>
          `;
        } else {
          authUserLabel.innerHTML = '';
          authUserLabel.removeAttribute('aria-label');
          authUserLabel.removeAttribute('title');
        }
      }
      if (logoutButton) logoutButton.hidden = !authenticated;
      if (settingsGitTab) settingsGitTab.hidden = !authenticated;
      if (!authenticated && settingsGitPanel && !settingsGitPanel.hidden) {
        setSettingsTab('general', { guardUnsaved: false });
      }
      const canManageUserAccounts = canManageUsers();
      const canEditRepositories = !isRepoDiscoveryReadonly();
      const canEditRuntimeRoles = canEditCommonSettings();
      if (settingsRolesTab) settingsRolesTab.hidden = !canEditRuntimeRoles;
      if (!canEditRuntimeRoles && settingsRolesPanel && !settingsRolesPanel.hidden) {
        setSettingsTab('general', { guardUnsaved: false });
      }
      if (settingsRepositoriesTab) settingsRepositoriesTab.hidden = !canEditRepositories;
      if (!canEditRepositories && settingsRepositoriesPanel && !settingsRepositoriesPanel.hidden) {
        setSettingsTab('general', { guardUnsaved: false });
      }
      if (settingsSlackTab) settingsSlackTab.hidden = !canEditRuntimeRoles;
      if (!canEditRuntimeRoles && settingsSlackPanel && !settingsSlackPanel.hidden) {
        setSettingsTab('slack-channel', { guardUnsaved: false });
      }
      if (settingsSlackChannelTab) settingsSlackChannelTab.hidden = false;
      if (settingsUsersTab) settingsUsersTab.hidden = !canManageUserAccounts;
      if (!canManageUserAccounts && settingsUsersPanel && !settingsUsersPanel.hidden) {
        setSettingsTab('general', { guardUnsaved: false });
      }
      updateUserManagementModeControls();
      updateSettingsPermissionControls();
      updateGitUnlockKeyStatus();
      if (activeTaskDetail) {
        updatePlanActionState();
        updateHumanVerificationState();
        updateTaskDeleteState();
      }
    }

    async function loadAuthState() {
      const response = await fetch('/api/auth/me');
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Failed to load auth state.');
      updateAuthControls(data);
      return data;
    }

    async function logout() {
      if (!logoutButton) return;
      logoutButton.disabled = true;
      try {
        await fetch('/api/auth/logout', { method: 'POST' });
      } finally {
        window.location.href = '/login';
      }
    }

    function setCreateUserStatus(message, tone = 'neutral') {
      if (!createUserStatus) return;
      createUserStatus.hidden = !message;
      createUserStatus.textContent = message || '';
      createUserStatus.dataset.tone = tone;
    }

    function setPasswordChangeStatus(message, tone = 'neutral') {
      if (!changePasswordStatus) return;
      changePasswordStatus.hidden = !message;
      changePasswordStatus.textContent = message || '';
      changePasswordStatus.dataset.tone = tone;
    }

    function renderUsers(users) {
      if (!settingsUserList) return;
      updateUserManagementModeControls();
      if (!isRemoteUsageActive()) {
        settingsUserList.innerHTML = '';
        return;
      }
      const rows = Array.isArray(users) ? users : [];
      if (!rows.length) {
        settingsUserList.innerHTML = `<div class="settings-user-card" style="grid-column: 1 / -1; justify-content: center; opacity: 0.7;"><span class="muted">${escapeHtml(translateSettings('userListEmpty'))}</span></div>`;
        return;
      }

      const avatarGradients = [
        'linear-gradient(135deg, #6366f1, #4f46e5)', // Indigo
        'linear-gradient(135deg, #ec4899, #be185d)', // Pink
        'linear-gradient(135deg, #10b981, #047857)', // Emerald
        'linear-gradient(135deg, #f59e0b, #d97706)', // Amber
        'linear-gradient(135deg, #3b82f6, #1d4ed8)', // Blue
        'linear-gradient(135deg, #8b5cf6, #6d28d9)'  // Violet
      ];
      const currentUserId = currentAuthUser()?.user_id || '';

      settingsUserList.innerHTML = rows.map((user) => {
        const username = user.username || '';
        const initial = username.charAt(0).toUpperCase();

        // Sum character codes to get a deterministic color index
        const charCodeSum = username.split('').reduce((acc, char) => acc + char.charCodeAt(0), 0);
        const bgGradient = avatarGradients[charCodeSum % avatarGradients.length];

        const roleClass = user.is_admin ? 'user-badge-admin' : 'user-badge-member';
        const roleLabel = user.is_admin ? translateSettings('userRoleAdmin') : translateSettings('userRoleMember');
        const isCurrentUser = user.user_id === currentUserId;
        const action = isCurrentUser
          ? `<button type="button" class="settings-user-delete" disabled>${escapeHtml(translateSettings('currentUserLabel'))}</button>`
          : `<button type="button" class="settings-user-delete" data-delete-user-id="${escapeHtml(user.user_id || '')}" data-delete-username="${escapeHtml(username)}">${escapeHtml(translateSettings('deleteUser'))}</button>`;

        return `
          <div class="settings-user-card">
            <div class="user-avatar" style="background: ${bgGradient};">${escapeHtml(initial)}</div>
            <div class="user-info">
              <strong class="user-username">${escapeHtml(username)}</strong>
              <span class="user-badge ${roleClass}">${escapeHtml(roleLabel)}</span>
            </div>
            ${action}
          </div>
        `;
      }).join('');
    }

    async function loadUsers() {
      if (!canManageUsers() || !settingsUserList) return;
      if (!isRemoteUsageActive()) {
        renderUsers([]);
        return;
      }
      const response = await fetch('/api/auth/users');
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || 'Failed to load users.');
      renderUsers(data.users || []);
    }

    function renderAccountSummary() {
      if (!accountSummary) return;
      const user = currentAuthUser();
      if (!user) {
        accountSummary.innerHTML = '';
        return;
      }
      const username = user.username || '';
      const initial = username.charAt(0).toUpperCase();
      const roleClass = user.is_admin ? 'user-badge-admin' : 'user-badge-member';
      const roleLabel = user.is_admin ? translateSettings('userRoleAdmin') : translateSettings('userRoleMember');
      accountSummary.innerHTML = `
        <div class="account-profile-hero">
          <div class="account-avatar-wrapper">
            <div class="user-avatar account-avatar" style="background: ${authAvatarGradient(username)};">${escapeHtml(initial)}</div>
            <span class="account-avatar-ring"></span>
          </div>
          <div class="account-summary-copy">
            <span class="account-signed-in-label">
              <svg class="secure-lock-icon" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="2.5" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" d="M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 0 2.25 2.25Z" />
              </svg>
              ${escapeHtml(translateSettings('accountSignedInAs'))}
            </span>
            <strong class="account-username">${escapeHtml(username)}</strong>
            <div class="account-badges-row">
              <span class="user-badge ${roleClass}">${escapeHtml(roleLabel)}</span>
              <span class="account-status-badge">
                <span class="status-pulse-dot"></span>
                Active Session
              </span>
            </div>
          </div>
        </div>
      `;
    }

    function openAccountModal() {
      if (!canChangeOwnPassword()) return;
      renderAccountSummary();
      resetAccountPasswordForm();
      setAccountModalOpen(true);
      currentUserPasswordInput?.focus();
    }

    async function changeOwnPassword() {
      if (!currentUserPasswordInput || !newUserPasswordChangeInput || !confirmUserPasswordChangeInput || !changePasswordButton) return;
      if (!canChangeOwnPassword()) {
        setPasswordChangeStatus(translateSettings('passwordChangeFailed'), 'error');
        return;
      }
      const currentPassword = currentUserPasswordInput.value || '';
      const newPassword = newUserPasswordChangeInput.value || '';
      const confirmPassword = confirmUserPasswordChangeInput.value || '';
      if (!currentPassword || !newPassword) {
        setPasswordChangeStatus(translateSettings('passwordChangeRequired'), 'error');
        return;
      }
      if (newPassword !== confirmPassword) {
        setPasswordChangeStatus(translateSettings('passwordChangeMismatch'), 'error');
        return;
      }
      changePasswordButton.disabled = true;
      setPasswordChangeStatus(translateSettings('passwordChangeRunning'));
      try {
        const response = await fetch('/api/auth/password', {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            current_password: currentPassword,
            new_password: newPassword,
          }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || translateSettings('passwordChangeFailed'));
        currentUserPasswordInput.value = '';
        newUserPasswordChangeInput.value = '';
        confirmUserPasswordChangeInput.value = '';
        setPasswordChangeStatus(translateSettings('passwordChangeSuccess'), 'success');
      } catch (error) {
        setPasswordChangeStatus(error.message || translateSettings('passwordChangeFailed'), 'error');
      } finally {
        changePasswordButton.disabled = false;
      }
    }

    async function createUser() {
      if (!newUserUsernameInput || !newUserPasswordInput || !newUserIsAdminInput || !createUserButton) return;
      const activationMode = isUserManagementActivationMode();
      if (!isRemoteUsageActive() && !activationMode) {
        setCreateUserStatus(translateSettings('remoteUsageDisabledHint'), 'error');
        updateUserManagementModeControls();
        return;
      }
      const username = (newUserUsernameInput.value || '').trim();
      const password = newUserPasswordInput.value || '';
      if (!username || !password) {
        setCreateUserStatus(currentUiLanguage() === 'KO' ? '사용자명과 비밀번호를 입력하세요.' : 'Enter a username and password.', 'error');
        return;
      }
      createUserButton.disabled = true;
      setCreateUserStatus(translateSettings(activationMode ? 'userManagementActivationRunning' : 'userCreateRunning'));
      try {
        const response = await fetch('/api/auth/users', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            username,
            password,
            is_admin: activationMode || Boolean(newUserIsAdminInput.checked),
          }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || translateSettings('userCreateFailed'));
        newUserUsernameInput.value = '';
        newUserPasswordInput.value = '';
        newUserIsAdminInput.checked = false;
        setCreateUserStatus(translateSettings(activationMode ? 'userManagementActivationSuccess' : 'userCreateSuccess'), 'success');
        remoteUsageSetupRequested = false;
        await loadAuthState();
        await loadUsers();
      } catch (error) {
        setCreateUserStatus(error.message || translateSettings('userCreateFailed'), 'error');
      } finally {
        createUserButton.disabled = false;
      }
    }

    async function deleteUser(userId, username) {
      if (!userId) return;
      const confirmed = window.confirm(translateSettings('userDeleteConfirm', { username: username || userId }));
      if (!confirmed) return;
      setCreateUserStatus(translateSettings('userDeleteRunning'));
      try {
        const response = await fetch(`/api/auth/users/${encodeURIComponent(userId)}`, { method: 'DELETE' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || translateSettings('userDeleteFailed'));
        setCreateUserStatus(translateSettings('userDeleteSuccess'), 'success');
        await loadUsers();
      } catch (error) {
        setCreateUserStatus(error.message || translateSettings('userDeleteFailed'), 'error');
      }
    }

    async function deleteAllUsers({ confirm = true } = {}) {
      const confirmed = !confirm || window.confirm(translateSettings('userDeleteAllConfirm'));
      if (!confirmed) return false;
      setCreateUserStatus(translateSettings('userDeleteAllRunning'));
      try {
        const response = await fetch('/api/auth/users', { method: 'DELETE' });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || translateSettings('userDeleteAllFailed'));
        setCreateUserStatus(translateSettings('userDeleteAllSuccess'), 'success');
        remoteUsageSetupRequested = false;
        window.location.href = '/';
        return true;
      } catch (error) {
        setCreateUserStatus(error.message || translateSettings('userDeleteAllFailed'), 'error');
        return false;
      }
    }

    async function handleRemoteUsageToggleChange() {
      if (!remoteUsageEnabledInput) return;
      if (remoteUsageEnabledInput.checked) {
        if (!isRemoteUsageActive()) {
          remoteUsageSetupRequested = true;
          updateUserManagementModeControls();
          newUserUsernameInput?.focus();
        }
        return;
      }
      if (isRemoteUsageActive()) {
        const disabled = await deleteAllUsers();
        if (!disabled) {
          remoteUsageEnabledInput.checked = true;
          updateUserManagementModeControls();
        }
        return;
      }
      remoteUsageSetupRequested = false;
      setCreateUserStatus('');
      updateUserManagementModeControls();
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

    function normalizeSettingsTab(tab) {
      const panels = ['general', 'git', 'repositories', 'roles', 'slack', 'slack-channel', 'users'];
      return panels.includes(tab) ? tab : 'general';
    }

    function settingsTabLabel(tab) {
      const keys = {
        general: 'settingsTabGeneral',
        git: 'settingsTabGit',
        repositories: 'settingsTabRepositories',
        roles: 'settingsTabRoles',
        slack: 'settingsTabSlack',
        'slack-channel': 'settingsTabSlackChannel',
        users: 'settingsTabUsers',
      };
      return translateSettings(keys[normalizeSettingsTab(tab)] || 'settingsTabGeneral');
    }

    function settingsStateFragmentForTab(tab, state) {
      const source = state || {};
      switch (normalizeSettingsTab(tab)) {
        case 'general':
          return {
            language: source.language || 'EN',
            theme: source.theme || 'light',
          };
        case 'git':
          return {
            git_token_username: source.git_token_username || '',
            git_token: source.git_token || '',
          };
        case 'repositories':
          return {
            repo_discovery_root: source.repo_discovery_root || '../',
            repo_discovery_max_depth: source.repo_discovery_max_depth || 1,
          };
        case 'roles':
          return {
            coding_assistant: source.coding_assistant || 'opencode',
            worker_live_logs_enabled: Boolean(source.worker_live_logs_enabled),
            role_backends: source.role_backends || {},
            planner_model: source.planner_model || '',
            request_draft_model: source.request_draft_model || '',
            planner_session_token_budget: source.planner_session_token_budget || 250,
            planner_agent_count: source.planner_agent_count || 1,
            plan_approval_model: source.plan_approval_model || '',
            plan_approval_session_token_budget: source.plan_approval_session_token_budget || 250,
            implementer_model: source.implementer_model || '',
            implementer_session_token_budget: source.implementer_session_token_budget || 250,
            implementer_agent_count: source.implementer_agent_count || 1,
            reviewer_model: source.reviewer_model || '',
            reviewer_session_token_budget: source.reviewer_session_token_budget || 250,
            reviewer_agent_count: source.reviewer_agent_count || 1,
            commit_model: source.commit_model || '',
            commit_session_token_budget: source.commit_session_token_budget || 250,
          };
        case 'slack':
          return {
            slack_enabled: Boolean(source.slack_enabled),
            slack_socket_mode_enabled: source.slack_socket_mode_enabled !== false,
            slack_app_mention_enabled: Boolean(source.slack_app_mention_enabled),
            slack_bot_name: source.slack_bot_name || '',
            slack_bot_token: source.slack_bot_token || '',
            slack_app_token: source.slack_app_token || '',
            slack_bot_token_clear_requested: Boolean(source.slack_bot_token_clear_requested),
            slack_app_token_clear_requested: Boolean(source.slack_app_token_clear_requested),
          };
        case 'slack-channel':
          return {
            slack_default_channel: source.slack_default_channel || '',
          };
        default:
          return {};
      }
    }

    function hasUnsavedSettingsTabChanges(tab = activeSettingsTab) {
      if (!lastLoadedSettingsState) return false;
      const normalizedTab = normalizeSettingsTab(tab);
      if (normalizedTab === 'users') return hasUnsavedUserManagementChanges();
      const currentFragment = settingsStateFragmentForTab(normalizedTab, captureSettingsState());
      const loadedFragment = settingsStateFragmentForTab(normalizedTab, lastLoadedSettingsState);
      return JSON.stringify(currentFragment) !== JSON.stringify(loadedFragment);
    }

    function firstUnsavedSettingsTab() {
      const tabs = ['general', 'git', 'repositories', 'roles', 'slack', 'slack-channel', 'users'];
      return tabs.find((tab) => {
        const tabEl = document.getElementById(`settings-tab-${tab}`);
        const panelEl = document.getElementById(`settings-panel-${tab}`);
        if (tab !== activeSettingsTab && tabEl?.hidden && panelEl?.hidden) return false;
        return hasUnsavedSettingsTabChanges(tab);
      }) || null;
    }

    function notifyUnsavedSettingsTab(tab = activeSettingsTab) {
      if (isOnboardingActive()) return;
      setSettingsStatus(translateSettings('statusUnsavedTab', { tab: settingsTabLabel(tab) }), 'error');
      const saveButton = saveSettingsButtons.find((button) => button.dataset.settingsSaveScope === normalizeSettingsTab(tab));
      if (saveButton && !saveButton.disabled) saveButton.focus({ preventScroll: true });
      if (!saveButton && normalizeSettingsTab(tab) === 'users') {
        const target = canChangeOwnPassword() ? currentUserPasswordInput : remoteUsageEnabledInput;
        target?.focus({ preventScroll: true });
      }
    }

    function confirmUnsavedSettingsBeforeClose() {
      const unsavedTab = firstUnsavedSettingsTab();
      if (!unsavedTab) return true;
      if (isOnboardingActive()) {
        restoreSettingsState(lastLoadedSettingsState);
        return true;
      }
      const confirmed = window.confirm(translateSettings('confirmUnsavedSettingsClose', { tab: settingsTabLabel(unsavedTab) }));
      if (!confirmed) notifyUnsavedSettingsTab(unsavedTab);
      return confirmed;
    }

    function setSettingsTab(tab, { guardUnsaved = true } = {}) {
      const targetTab = normalizeSettingsTab(tab);
      if (guardUnsaved && targetTab !== activeSettingsTab && hasUnsavedSettingsTabChanges(activeSettingsTab)) {
        const currentTab = activeSettingsTab;
        if (isOnboardingActive()) {
          restoreSettingsState(lastLoadedSettingsState);
        } else {
          const confirmed = window.confirm(translateSettings('confirmUnsavedSettingsTabLeave', { tab: settingsTabLabel(currentTab) }));
          if (!confirmed) {
            notifyUnsavedSettingsTab(currentTab);
            return false;
          }
          restoreSettingsState(lastLoadedSettingsState);
        }
      }
      activeSettingsTab = targetTab;
      if (typeof setSettingsStatus === 'function') {
        setSettingsStatus('');
      }
      const panels = ['general', 'git', 'repositories', 'roles', 'slack', 'slack-channel', 'users'];
      panels.forEach(p => {
        const active = p === targetTab;
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
      updateSettingsStatusVisibility(targetTab);
      return true;
    }
    window.setSettingsTab = setSettingsTab;


    document.getElementById('settings-tab-general').addEventListener('click', () => setSettingsTab('general'));
    if (settingsGitTab) settingsGitTab.addEventListener('click', () => setSettingsTab('git'));
    document.getElementById('settings-tab-slack').addEventListener('click', () => setSettingsTab('slack'));
    if (settingsSlackChannelTab) settingsSlackChannelTab.addEventListener('click', () => setSettingsTab('slack-channel'));
    if (settingsRolesTab) settingsRolesTab.addEventListener('click', () => setSettingsTab('roles'));
    if (settingsRepositoriesTab) settingsRepositoriesTab.addEventListener('click', () => setSettingsTab('repositories'));
    if (settingsUsersTab) settingsUsersTab.addEventListener('click', () => {
      if (!setSettingsTab('users')) return;
      loadUsers().catch((error) => setCreateUserStatus(error.message, 'error'));
    });
    if (remoteUsageEnabledInput) remoteUsageEnabledInput.addEventListener('change', () => {
      handleRemoteUsageToggleChange().catch((error) => {
        setCreateUserStatus(error.message || translateSettings('userDeleteAllFailed'), 'error');
        updateUserManagementModeControls();
      });
    });
    if (settingsUserList) settingsUserList.addEventListener('click', (event) => {
      const button = event.target.closest('[data-delete-user-id]');
      if (!button || button.disabled) return;
      deleteUser(button.dataset.deleteUserId || '', button.dataset.deleteUsername || '').catch((error) => {
        setCreateUserStatus(error.message || translateSettings('userDeleteFailed'), 'error');
      });
    });
