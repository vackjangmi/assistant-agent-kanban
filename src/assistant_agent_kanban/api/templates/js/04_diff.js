    function isDesktopDiffLayout() {
      return window.innerWidth > 900;
    }

    function readTaskChangedFilesPaneWidth() {
      try {
        const value = Number.parseFloat(window.localStorage.getItem(taskChangedFilesPaneWidthStorageKey) || '');
        return Number.isFinite(value) ? value : taskChangedFilesPaneDefaultWidth;
      } catch (_error) {
        return taskChangedFilesPaneDefaultWidth;
      }
    }

    function taskChangedFilesPaneMaxWidth() {
      const layoutWidth = taskChangedFilesLayout?.clientWidth || window.innerWidth || taskChangedFilesPaneDefaultWidth;
      const viewportMax = Math.floor(window.innerWidth * 0.45);
      const layoutMax = Math.floor(layoutWidth - 280);
      return Math.max(taskChangedFilesPaneMinWidth, Math.min(viewportMax, layoutMax));
    }

    function clampTaskChangedFilesPaneWidth(width) {
      const numericWidth = Number(width);
      if (!Number.isFinite(numericWidth)) return taskChangedFilesPaneDefaultWidth;
      return Math.min(taskChangedFilesPaneMaxWidth(), Math.max(taskChangedFilesPaneMinWidth, Math.round(numericWidth)));
    }

    function applyTaskChangedFilesPaneWidth(width, options = {}) {
      if (!taskChangedFilesLayout) return taskChangedFilesPaneDefaultWidth;
      const clampedWidth = clampTaskChangedFilesPaneWidth(width);
      taskChangedFilesLayout.style.setProperty('--task-changed-files-width', `${clampedWidth}px`);
      if (options.persist) {
        try {
          window.localStorage.setItem(taskChangedFilesPaneWidthStorageKey, String(clampedWidth));
        } catch (_error) {
        }
      }
      return clampedWidth;
    }

    function syncTaskChangedFilesPaneWidth() {
      return applyTaskChangedFilesPaneWidth(readTaskChangedFilesPaneWidth());
    }

    function updateTaskChangedFilesPaneWidthFromClientX(clientX, options = {}) {
      if (!taskChangedFilesLayout) return taskChangedFilesPaneDefaultWidth;
      const layoutRect = taskChangedFilesLayout.getBoundingClientRect();
      return applyTaskChangedFilesPaneWidth(clientX - layoutRect.left, options);
    }

    syncTaskChangedFilesPaneWidth();

    function currentTargetRepoOptions() {
      return Array.from(targetRepoOptions.querySelectorAll('option')).map((option) => option.value).filter(Boolean);
    }

    function applyTargetRepoAutofill(items) {
      const options = Array.isArray(items) ? items : [];
      const currentValue = normalizeRepoPath(targetRepoInput.value);
      const canAutofill = !currentValue || targetRepoInput.dataset.autofilled === 'true';
      if (!canAutofill) return;
      const storedTargetRepo = readLastTargetRepo();
      const nextValue = storedTargetRepo || options[0] || defaultTargetRepo;
      targetRepoInput.value = nextValue;
      targetRepoInput.dataset.autofilled = nextValue ? 'true' : 'false';
      if (!nextValue) {
        invalidateBranchLookup();
        replaceBaseBranchSuggestions([]);
        updateBaseBranchHelp(translateRequest('baseBranchHelp'));
        return;
      }
      applyRepoDefaults();
      queueTargetRepoBranchLookup();
    }

    function invalidateBranchLookup() {
      latestBranchLookupToken += 1;
      if (branchLookupTimer) {
        clearTimeout(branchLookupTimer);
        branchLookupTimer = null;
      }
    }

    function maybeAutofillBaseBranch(nextValue) {
      if (!nextValue) return;
      if (canReplaceAutofill(baseBranchInput, nextValue, lastAutoBaseBranch)) {
        baseBranchInput.value = nextValue;
        baseBranchInput.dataset.autofilled = 'true';
      }
      lastAutoBaseBranch = nextValue;
    }

    async function loadTargetRepoBranches() {
      invalidateBranchLookup();
      const repoPath = normalizeRepoPath(targetRepoInput.value);
      const lookupToken = latestBranchLookupToken;
      replaceBaseBranchSuggestions([]);
      if (!repoPath) {
        updateBaseBranchHelp(translateRequest('baseBranchHelp'));
        maybeAutofillBaseBranch(defaultBaseBranch);
        return;
      }
      updateBaseBranchHelp(translateRequest('baseBranchLoading'));
      try {
        const response = await fetch(`/api/target-repo-branches?target_repo=${encodeURIComponent(repoPath)}`);
        const data = await response.json();
        if (lookupToken !== latestBranchLookupToken) return;
        if (!response.ok) throw new Error(data.detail || translateRequest('baseBranchLoadFailed'));
        replaceBaseBranchSuggestions(data.branches || []);
        maybeAutofillBaseBranch(data.suggested_base_branch || defaultBaseBranch);
        if (!data.git_repository) {
          updateBaseBranchHelp(translateRequest('baseBranchNotRepo'));
          return;
        }
        if (!data.branches || !data.branches.length) {
          updateBaseBranchHelp(translateRequest('baseBranchNoSuggestions'));
          return;
        }
        const currentNote = data.current_branch ? translateRequest('currentBranchNote', { branch: data.current_branch }) : '';
        updateBaseBranchHelp(translateRequest(data.branches.length === 1 ? 'baseBranchLoadedOne' : 'baseBranchLoadedMany', { count: data.branches.length, currentNote }));
      } catch (error) {
        if (lookupToken !== latestBranchLookupToken) return;
        replaceBaseBranchSuggestions([]);
        updateBaseBranchHelp(error.message || translateRequest('baseBranchLoadFailed'));
      }
    }

    function queueTargetRepoBranchLookup() {
      if (branchLookupTimer) clearTimeout(branchLookupTimer);
      branchLookupTimer = window.setTimeout(loadTargetRepoBranches, 250);
    }

    function setSettingsModalOpen(isOpen) {
      settingsModal.hidden = !isOpen;
      settingsModal.setAttribute('aria-hidden', String(!isOpen));
      syncBodyModalState();
      if (!isOpen) settingsRequestToken += 1;
      if (!isOpen && slackReceiveTestPollTimer) {
        clearTimeout(slackReceiveTestPollTimer);
        slackReceiveTestPollTimer = null;
      }
      if (isOpen) plannerModelSelectInput.focus();
    }

    function setSettingsStatus(message, tone = 'neutral') {
      settingsStatus.textContent = message;
      settingsStatus.dataset.tone = tone;
    }

    function setSettingsFormHydrating(isHydrating) {
      Array.from(settingsForm.elements).forEach((element) => {
        if (!(element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element instanceof HTMLTextAreaElement || element instanceof HTMLButtonElement)) return;
        element.disabled = isHydrating;
      });
      saveSettingsButton.disabled = isHydrating;
    }

    function syncNumericSettingInput(input, value, fallback) {
      const normalized = Number.isFinite(value) && value > 0 ? Math.floor(value) : fallback;
      input.value = String(normalized);
      input.dataset.currentValue = String(normalized);
    }

    function readNumericSettingInput(input, fallback) {
      const parsed = Number.parseInt(input.value || '', 10);
      if (Number.isFinite(parsed) && parsed > 0) return parsed;
      const currentValue = Number.parseInt(input.dataset.currentValue || '', 10);
      return Number.isFinite(currentValue) && currentValue > 0 ? currentValue : fallback;
    }

    function updateModelDiscoverySummary(data) {
      lastSettingsPayload = data;
      cachedAssistantOptions = resolveAssistantOptions(data);
      const activeBackend = runtimeCodingAssistantInput.value || data.coding_assistant || 'opencode';
      const availability = data.backend_availability_by_backend?.[activeBackend];
      if (availability && availability.available === false) {
        settingsDiscoverySummary.textContent = translateSettings('summaryEmpty');
        setSettingsStatus(translateSettings('errorBackendUnavailable', { field: activeBackend, message: availability.error || 'not installed' }), 'error');
        return;
      }
      const count = data.available_models.length;
      if (count) {
        const refreshedAt = data.discovered_at ? new Date(data.discovered_at).toLocaleString() : translateSettings('justNow');
        settingsDiscoverySummary.textContent = translateSettings(count === 1 ? 'summaryAvailableOne' : 'summaryAvailableMany', { count, refreshedAt });
        if (data.discovery_status === 'fallback' && data.discovery_error) {
          setSettingsStatus(translateSettings('statusFallback', { error: data.discovery_error }), 'error');
        } else {
          setSettingsStatus(translateSettings('statusLoaded'), 'success');
        }
        return;
      }
      settingsDiscoverySummary.textContent = translateSettings('summaryEmpty');
      if (data.discovery_status === 'error' && data.discovery_error) {
        setSettingsStatus(translateSettings('statusDiscoveryFailed', { error: data.discovery_error }), 'error');
        return;
      }
      if (data.discovery_status === 'empty') {
        setSettingsStatus(translateSettings('statusDiscoveryEmpty'));
        return;
      }
      setSettingsStatus(translateSettings('statusLoadedHint'));
    }

    function mergeSettingsPayload(data) {
      if (!lastSettingsPayload?.available_models_by_backend) return data;
      return {
        ...lastSettingsPayload,
        ...data,
        available_models_by_backend: {
          ...(lastSettingsPayload.available_models_by_backend || {}),
          ...(data.available_models_by_backend || {}),
        },
        backend_availability_by_backend: {
          ...(lastSettingsPayload.backend_availability_by_backend || {}),
          ...(data.backend_availability_by_backend || {}),
        },
      };
    }

    function hydrateSettingsDiscovery(data, { preserveState = false, updateSummary = true } = {}) {
      const mergedData = mergeSettingsPayload(data);
      const preservedState = preserveState ? captureSettingsState() : null;
      if (preserveState && preservedState) {
        if (updateSummary) updateModelDiscoverySummary(mergedData);
        else {
          lastSettingsPayload = mergedData;
          cachedAssistantOptions = resolveAssistantOptions(mergedData);
        }
        restoreSettingsState(preservedState);
        if (!updateSummary) setSettingsStatus(translateSettings('statusLoaded'), 'success');
        return;
      }
      if (!updateSummary) {
        lastSettingsPayload = mergedData;
        cachedAssistantOptions = resolveAssistantOptions(mergedData);
      }
      applyLoadedModelSettings(mergedData);
      if (!updateSummary) setSettingsStatus(translateSettings('statusLoaded'), 'success');
    }

    function applyLoadedModelSettings(data) {
      runtimeLanguageInput.value = data.language || 'EN';
      runtimeThemeInput.value = data.theme || 'light';
      applyRuntimeTheme(runtimeThemeInput.value);
      cachedAssistantOptions = resolveAssistantOptions(data);
      renderAssistantOptions(cachedAssistantOptions, data.coding_assistant || 'opencode');
      applySlackSettingsData(data);
      plannerBackendInput.value = data.role_backends?.planner || 'default';
      requestDraftBackendInput.value = data.role_backends?.request_draft || 'default';
      planApprovalBackendInput.value = data.role_backends?.plan_approval || 'default';
      implementerBackendInput.value = data.role_backends?.implementer || 'default';
      reviewerBackendInput.value = data.role_backends?.reviewer || 'default';
      commitBackendInput.value = data.role_backends?.commit || 'default';
      updateWorkerLiveLogsControlVisibility();
      workerLiveLogsModeInput.value = data.worker_live_logs_enabled ? 'true' : 'false';
      applyRuntimeSettingsTranslations();
      repoDiscoveryRootInput.value = data.repo_discovery_root || '../';
      syncNumericSettingInput(repoDiscoveryMaxDepthInput, data.repo_discovery_max_depth, 2);
      setRoleModelValue('planner', data.planner_model || '');
      setRoleModelValue('request_draft', data.request_draft_model || '');
      syncNumericSettingInput(plannerSessionTokenBudgetInput, data.planner_session_token_budget, 250);
      syncNumericSettingInput(plannerAgentCountInput, data.planner_agent_count, 1);
      setRoleModelValue('plan_approval', data.plan_approval_model || '');
      syncNumericSettingInput(planApprovalSessionTokenBudgetInput, data.plan_approval_session_token_budget, 250);
      setRoleModelValue('implementer', data.implementer_model || '');
      syncNumericSettingInput(implementerSessionTokenBudgetInput, data.implementer_session_token_budget, 250);
      syncNumericSettingInput(implementerAgentCountInput, data.implementer_agent_count, 1);
      setRoleModelValue('reviewer', data.reviewer_model || '');
      syncNumericSettingInput(reviewerSessionTokenBudgetInput, data.reviewer_session_token_budget, 250);
      syncNumericSettingInput(reviewerAgentCountInput, data.reviewer_agent_count, 1);
      setRoleModelValue('commit', data.commit_model || '');
      syncNumericSettingInput(commitSessionTokenBudgetInput, data.commit_session_token_budget, 250);
      if (!targetRepoOptionsLoaded) {
        void loadTargetRepoOptions().catch(() => {});
      }
      updateModelDiscoverySummary(data);
      renderAllRoleModelOptions();
      lastLoadedSettingsState = captureSettingsState();
    }

    async function loadModelSettings(refresh = false, options = {}) {
      const requestToken = ++settingsRequestToken;
      const allowHidden = Boolean(options.allowHidden);
      const preserveState = Boolean(options.preserveState);
      const assistantOverride = typeof options.assistantOverride === 'string' ? options.assistantOverride : '';
      const updateSummary = options.updateSummary !== false;
      if (!refresh && !allowHidden) setSettingsFormHydrating(true);
      setSettingsStatus(refresh ? translateSettings('statusRefreshing') : translateSettings('statusLoading'));
      refreshModelOptionsButton.disabled = true;
      try {
        const params = new URLSearchParams();
        if (refresh) params.set('refresh', 'true');
        const selectedAssistant = assistantOverride || runtimeCodingAssistantInput.value || 'opencode';
        if (refresh && selectedAssistant) params.set('assistant', selectedAssistant);
        const query = params.toString();
        const response = await fetch(`/api/settings/models${query ? `?${query}` : ''}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || translateSettings('settingsLoadFailed'));
        if (requestToken !== settingsRequestToken || (!allowHidden && settingsModal.hidden)) return;
        hydrateSettingsDiscovery(data, { preserveState, updateSummary });
      } finally {
        if (!refresh && !allowHidden && requestToken === settingsRequestToken && !settingsModal.hidden) {
          setSettingsFormHydrating(false);
        }
        refreshModelOptionsButton.disabled = false;
      }
    }

    async function openSettingsModal() {
      setSettingsModalOpen(true);
      if (!lastSettingsPayload) {
        void loadModelSettings(false).catch((error) => {
          setSettingsStatus(error.message, 'error');
          setSettingsFormHydrating(false);
        });
      }
      void pollSlackReceiveTestStatus().catch(() => {});
    }

    async function runSlackSettingsTest() {
      testSlackSettingsButton.disabled = true;
      setSlackSettingsTestStatus({ summary: translateSettings('slackTestRunning'), checks: [] });
      try {
        if (hasUnsavedSlackChannelActivationDependencies()) {
          throw new Error(translateSettings('slackTestSaveFirst'));
        }
        const response = await fetch('/api/settings/slack-test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(buildSlackSettingsPayload()),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Slack test failed.');
        if (data.ok) {
          await loadModelSettings(false);
        }
        setSlackSettingsTestStatus(data, data.ok ? 'success' : 'error');
      } catch (error) {
        setSlackSettingsTestStatus({ summary: error.message || 'Slack test failed.', checks: [] }, 'error');
      } finally {
        testSlackSettingsButton.disabled = false;
      }
    }

    async function startSlackReceiveTest() {
      startSlackReceiveTestButton.disabled = true;
      if (hasUnsavedSlackReceiveSettings()) {
        updateSlackReceiveTestStatus({ listener_enabled: false, listener_connected: false, listener_last_error: translateSettings('slackReceiveSaveFirst'), receive_test: null });
        startSlackReceiveTestButton.disabled = false;
        return;
      }
      if (slackAdvancedDetails) slackAdvancedDetails.open = true;
      updateSlackReceiveTestStatus({ listener_enabled: true, listener_connected: false, listener_last_error: null, receive_test: { status: 'pending', instruction: translateSettings('slackReceivePreparing') } });
      try {
        const response = await fetch('/api/settings/slack-receive-test/start', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Slack receive test failed to start.');
        updateSlackReceiveTestStatus(data);
        await pollSlackReceiveTestStatus();
      } catch (error) {
        updateSlackReceiveTestStatus({ listener_enabled: false, listener_connected: false, listener_last_error: error.message || 'Slack receive test failed to start.', receive_test: null });
      } finally {
        startSlackReceiveTestButton.disabled = false;
      }
    }

    async function saveModelSettings(event) {
      event.preventDefault();
      settingsRequestToken += 1;
      saveSettingsButton.disabled = true;
      setSettingsStatus(translateSettings('statusSaving'));
      try {
        const slackPayload = buildSlackSettingsPayload();
        const pendingSlackChannel = slackPayload.slack_default_channel || '';
        const response = await fetch('/api/settings/models', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            language: runtimeLanguageInput.value || 'EN',
            theme: runtimeThemeInput.value || 'light',
            coding_assistant: runtimeCodingAssistantInput.value || 'opencode',
            role_backends: {
              planner: plannerBackendInput.value === 'default' ? null : plannerBackendInput.value,
              request_draft: requestDraftBackendInput.value === 'default' ? null : requestDraftBackendInput.value,
              plan_approval: planApprovalBackendInput.value === 'default' ? null : planApprovalBackendInput.value,
              implementer: implementerBackendInput.value === 'default' ? null : implementerBackendInput.value,
              reviewer: reviewerBackendInput.value === 'default' ? null : reviewerBackendInput.value,
              commit: commitBackendInput.value === 'default' ? null : commitBackendInput.value,
            },
            worker_live_logs_enabled: workerLiveLogsModeInput.value === 'true',
            repo_discovery_root: repoDiscoveryRootInput.value,
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
            ...slackPayload,
          }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(formatSettingsApiError(data.detail));
        runtimeLanguageInput.value = data.language || 'EN';
        runtimeThemeInput.value = data.theme || 'light';
        applyRuntimeTheme(runtimeThemeInput.value);
        cachedAssistantOptions = resolveAssistantOptions(data);
        renderAssistantOptions(cachedAssistantOptions, data.coding_assistant || 'opencode');
        const preservePendingChannel = Boolean(normalizeSlackChannelValue(pendingSlackChannel))
          && normalizeSlackChannelValue(pendingSlackChannel) !== normalizeSlackChannelValue(data.slack_default_channel_display || data.slack_default_channel || '');
        applySlackSettingsData(data, { preserveChannelInput: preservePendingChannel });
        plannerBackendInput.value = data.role_backends?.planner || 'default';
        requestDraftBackendInput.value = data.role_backends?.request_draft || 'default';
        planApprovalBackendInput.value = data.role_backends?.plan_approval || 'default';
        implementerBackendInput.value = data.role_backends?.implementer || 'default';
        reviewerBackendInput.value = data.role_backends?.reviewer || 'default';
        commitBackendInput.value = data.role_backends?.commit || 'default';
        updateWorkerLiveLogsControlVisibility();
        workerLiveLogsModeInput.value = data.worker_live_logs_enabled ? 'true' : 'false';
        applyRuntimeSettingsTranslations();
        repoDiscoveryRootInput.value = data.repo_discovery_root || '../';
        syncNumericSettingInput(repoDiscoveryMaxDepthInput, data.repo_discovery_max_depth, 2);
        setRoleModelValue('planner', data.planner_model || '');
        setRoleModelValue('request_draft', data.request_draft_model || '');
        syncNumericSettingInput(plannerSessionTokenBudgetInput, data.planner_session_token_budget, 250);
        syncNumericSettingInput(plannerAgentCountInput, data.planner_agent_count, 1);
        setRoleModelValue('plan_approval', data.plan_approval_model || '');
        syncNumericSettingInput(planApprovalSessionTokenBudgetInput, data.plan_approval_session_token_budget, 250);
        setRoleModelValue('implementer', data.implementer_model || '');
        syncNumericSettingInput(implementerSessionTokenBudgetInput, data.implementer_session_token_budget, 250);
        syncNumericSettingInput(implementerAgentCountInput, data.implementer_agent_count, 1);
        setRoleModelValue('reviewer', data.reviewer_model || '');
        syncNumericSettingInput(reviewerSessionTokenBudgetInput, data.reviewer_session_token_budget, 250);
        syncNumericSettingInput(reviewerAgentCountInput, data.reviewer_agent_count, 1);
        setRoleModelValue('commit', data.commit_model || '');
        syncNumericSettingInput(commitSessionTokenBudgetInput, data.commit_session_token_budget, 250);
        if (!targetRepoOptionsLoaded) {
          void loadTargetRepoOptions().catch(() => {});
        }
        updateModelDiscoverySummary(data);
        renderAllRoleModelOptions();
        setSettingsStatus(translateSettings('statusSaved'), 'success');
        lastLoadedSettingsState = captureSettingsState();
        try {
          await loadBoard();
          scheduleActiveTaskRefresh({ reloadArtifact: false });
        } catch (refreshError) {
          console.warn('Settings saved, but the board refresh failed.', refreshError);
        }
      } catch (error) {
        setSettingsStatus(error.message, 'error');
      } finally {
        saveSettingsButton.disabled = false;
      }
    }

    let requestModalFocusToken = 0;

    function focusRequestFieldForValidation(fieldName) {
      if (fieldName === 'goal') {
        requestGoalEditor?.focus?.();
        if (!requestGoalEditor && !requestGoalEditorHost.hidden) requestGoalEditorHost.focus?.();
        if (!requestGoalEditorFallback.hidden) requestGoalEditorFallback.focus();
        return;
      }
      const field = requestForm.elements.namedItem(fieldName);
      if (field && 'focus' in field) field.focus();
    }

    function focusRequestTitleWhenReady(token) {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          if (token !== requestModalFocusToken || modal.hidden) return;
          requestTitleInput.focus();
        });
      });
    }

    function setModalOpen(isOpen) {
      modal.hidden = !isOpen;
      modal.setAttribute('aria-hidden', String(!isOpen));
      syncBodyModalState();
      requestModalFocusToken += 1;
      if (isOpen) focusRequestTitleWhenReady(requestModalFocusToken);
    }

    function setRequestComposerTab(tab) {
      activeRequestComposerTab = tab === 'assistant' ? 'assistant' : 'fields';
      const showingFields = activeRequestComposerTab === 'fields';
      requestComposerTabFields.classList.toggle('active', showingFields);
      requestComposerTabAssistant.classList.toggle('active', !showingFields);
      requestComposerTabFields.setAttribute('aria-selected', String(showingFields));
      requestComposerTabAssistant.setAttribute('aria-selected', String(!showingFields));
      requestComposerTabFields.tabIndex = showingFields ? 0 : -1;
      requestComposerTabAssistant.tabIndex = showingFields ? -1 : 0;
      requestComposerPanelFields.hidden = !showingFields;
      requestComposerPanelAssistant.hidden = showingFields;
      void syncRequestComposerDraftState({ silent: true });
    }

    function setTaskModalOpen(isOpen) {
      taskModal.hidden = !isOpen;
      taskModal.setAttribute('aria-hidden', String(!isOpen));
      if (!isOpen) {
        setApprovalChoiceModalOpen(false, { force: true });
        setResumePlannerChoiceModalOpen(false, { force: true });
        setResumeImplementerChoiceModalOpen(false, { force: true });
        setResumeReviewerChoiceModalOpen(false, { force: true });
        clearTaskRefreshTimer();
        clearReviewerQaRefreshInterval();
        activeTaskRequestToken += 1;
        activeTaskLogRequestToken += 1;
        taskDetailStale = false;
        activeTaskLogs = null;
        activeLogName = null;
        activeInlineCommentAnchor = null;
        reviewerQaPendingQuestion = '';
        reviewerQaPendingAnswer = '';
        reviewerQaQuestionInFlight = false;
        reviewerQaDraftBackup = '';
        reviewerQaLastRenderedSignature = '';
      }
      updateReviewerQaLiveRefresh();
      syncBodyModalState();
    }

    function setApprovalChoiceModalOpen(isOpen, { force = false } = {}) {
      if (!isOpen && approvalSubmissionInFlight && !force) return;
      approvalChoiceModal.hidden = !isOpen;
      approvalChoiceModal.setAttribute('aria-hidden', String(!isOpen));
      if (!isOpen) {
        approvalChoiceStatus.hidden = true;
        approvalChoiceStatus.textContent = '';
        approvalChoiceStatus.dataset.tone = 'neutral';
        approvalChoiceTargetButton.disabled = false;
        approvalChoiceNewBranchButton.disabled = false;
        closeApprovalChoiceButton.disabled = false;
      }
      syncBodyModalState();
    }

    function setResumePlannerChoiceModalOpen(isOpen, { force = false } = {}) {
      if (!isOpen && resumePlannerSubmissionInFlight && !force) return;
      resumePlannerChoiceModal.hidden = !isOpen;
      resumePlannerChoiceModal.setAttribute('aria-hidden', String(!isOpen));
      if (isOpen) resumePlannerMessageInput.focus();
      if (!isOpen) {
        resumePlannerMessageInput.value = '';
        resumePlannerChoiceStatus.hidden = true;
        resumePlannerChoiceStatus.textContent = '';
        resumePlannerChoiceStatus.dataset.tone = 'neutral';
        resumePlannerChoiceButton.disabled = false;
        closeResumePlannerChoiceButton.disabled = false;
      }
      syncBodyModalState();
    }

    function setResumeImplementerChoiceModalOpen(isOpen, { force = false } = {}) {
      if (!isOpen && resumeImplementerSubmissionInFlight && !force) return;
      resumeImplementerChoiceModal.hidden = !isOpen;
      resumeImplementerChoiceModal.setAttribute('aria-hidden', String(!isOpen));
      if (isOpen) resumeImplementerMessageInput.focus();
      if (!isOpen) {
        resumeImplementerMessageInput.value = '';
        resumeImplementerChoiceStatus.hidden = true;
        resumeImplementerChoiceStatus.textContent = '';
        resumeImplementerChoiceStatus.dataset.tone = 'neutral';
        resumeImplementerChoicePinnedButton.disabled = false;
        resumeImplementerChoiceCurrentButton.disabled = false;
        closeResumeImplementerChoiceButton.disabled = false;
      }
      syncBodyModalState();
    }

    function setResumeReviewerChoiceModalOpen(isOpen, { force = false } = {}) {
      if (!isOpen && resumeReviewerSubmissionInFlight && !force) return;
      resumeReviewerChoiceModal.hidden = !isOpen;
      resumeReviewerChoiceModal.setAttribute('aria-hidden', String(!isOpen));
      if (isOpen) resumeReviewerMessageInput.focus();
      if (!isOpen) {
        resumeReviewerMessageInput.value = '';
        resumeReviewerChoiceStatus.hidden = true;
        resumeReviewerChoiceStatus.textContent = '';
        resumeReviewerChoiceStatus.dataset.tone = 'neutral';
        resumeReviewerChoicePinnedButton.disabled = false;
        resumeReviewerChoiceCurrentButton.disabled = false;
        closeResumeReviewerChoiceButton.disabled = false;
      }
      syncBodyModalState();
    }

    function setRetrospectiveModalOpen(isOpen) {
      retrospectiveModal.hidden = !isOpen;
      retrospectiveModal.setAttribute('aria-hidden', String(!isOpen));
      if (!isOpen) {
        activeRetrospectiveTargetRepoRoot = '';
        activeRetrospectiveBaseBranch = '';
        activeRetrospectiveComparisonBranch = '';
        activeRetrospectiveRecord = null;
        retrospectiveCreateTargetButton.hidden = true;
        retrospectiveCreateBranchButton.hidden = true;
        retrospectiveMeta.innerHTML = '';
        retrospectiveContent.textContent = translateTask('retrospectiveNoContent');
        retrospectiveStatus.dataset.tone = 'neutral';
        retrospectiveStatus.textContent = translateTask('retrospectiveIdle');
        retrospectiveCompareBranchInput.value = '';
        retrospectiveCompareOptions.innerHTML = '';
        setRetrospectiveMode('choice');
      }
      syncBodyModalState();
    }

    function syncBodyModalState() {
      body.classList.toggle('modal-open', !modal.hidden || !settingsModal.hidden || !taskModal.hidden || !retrospectiveModal.hidden || !approvalChoiceModal.hidden || !resumeImplementerChoiceModal.hidden || !resumeReviewerChoiceModal.hidden);
    }

    function renderRetrospectiveMeta(record) {
      const items = [
        [translateTask('retrospectiveCompareMetaLabel'), record.comparison_branch || ''],
        [translateTask('retrospectiveCommitBranchLabel'), record.committed_branch || ''],
        [translateTask('retrospectivePathLabel'), record.repo_relative_path || ''],
        [translateTask('retrospectiveModelLabel'), record.resolved_model || ''],
      ].filter(([, value]) => value);
      retrospectiveMeta.innerHTML = items.map(([label, value]) => `<div class="retrospective-meta-item"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join('');
    }

    function updateRetrospectiveButtons(record) {
      const shouldOfferCreate = !record?.exists && record?.can_create !== false && !!activeRetrospectiveTargetRepoRoot && !!activeRetrospectiveBaseBranch;
      retrospectiveCreateTargetButton.hidden = !shouldOfferCreate;
      retrospectiveCreateBranchButton.hidden = !shouldOfferCreate;
      retrospectiveCreateTargetButton.disabled = false;
      retrospectiveCreateBranchButton.disabled = false;
    }

    function renderRetrospective(record, statusKey, tone = 'neutral') {
      activeRetrospectiveRecord = record;
      retrospectiveStatus.hidden = statusKey === 'retrospectiveMissing';
      retrospectiveStatus.dataset.tone = tone;
      retrospectiveStatus.textContent = translateTask(statusKey);
      setRetrospectiveMode(record?.exists ? 'view' : 'choice', record || null);
      renderRetrospectiveMeta(record?.exists ? record : {});
      retrospectiveContent.textContent = record?.exists ? (record.content || translateTask('retrospectiveNoContent')) : '';
      updateRetrospectiveButtons(record || {});
    }

    async function inspectRetrospective(targetRepoRoot, baseBranch) {
      const response = await fetch('/api/retrospectives/inspect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_repo_root: targetRepoRoot, base_branch: baseBranch, comparison_branch: normalizedRetrospectiveComparisonBranch() || null }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || translateTask('failedApproveVerification'));
      return payload;
    }

    async function createRetrospective(completionMode) {
      const statusKey = completionMode === 'target-branch' ? 'retrospectiveCreatingTarget' : 'retrospectiveCreatingBranch';
      retrospectiveStatus.dataset.tone = 'neutral';
      retrospectiveStatus.textContent = translateTask(statusKey);
      retrospectiveCreateTargetButton.disabled = true;
      retrospectiveCreateBranchButton.disabled = true;
      const response = await fetch('/api/retrospectives/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_repo_root: activeRetrospectiveTargetRepoRoot, base_branch: activeRetrospectiveBaseBranch, comparison_branch: normalizedRetrospectiveComparisonBranch() || null, completion_mode: completionMode }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || translateTask('failedApproveVerification'));
      const resultStatusKey = payload.created
        ? (completionMode === 'target-branch' ? 'retrospectiveCreatedTarget' : 'retrospectiveCreatedBranch')
        : (payload.completion_mode === 'target-branch' ? 'retrospectiveLoadedTarget' : 'retrospectiveLoadedBranch');
      renderRetrospective(payload, resultStatusKey, 'success');
      await loadBoard();
    }

    async function openRetrospectiveModal(targetRepoRoot, baseBranch) {
      activeRetrospectiveTargetRepoRoot = targetRepoRoot;
      activeRetrospectiveBaseBranch = baseBranch;
      activeRetrospectiveComparisonBranch = '';
      retrospectiveCompareBranchInput.value = '';
      setRetrospectiveMode('choice', {
        target_repo_root: targetRepoRoot,
        base_branch: baseBranch,
      });
      await loadRetrospectiveCompareBranchOptions(targetRepoRoot, baseBranch);
      retrospectiveStatus.hidden = true;
      retrospectiveStatus.dataset.tone = 'neutral';
      retrospectiveStatus.textContent = translateTask('retrospectiveLoading');
      retrospectiveMeta.innerHTML = '';
      retrospectiveContent.textContent = '';
      retrospectiveCreateTargetButton.hidden = false;
      retrospectiveCreateBranchButton.hidden = false;
      retrospectiveCreateTargetButton.disabled = false;
      retrospectiveCreateBranchButton.disabled = false;
      setRetrospectiveModalOpen(true);
      try {
        const record = await inspectRetrospective(targetRepoRoot, baseBranch);
        const statusKey = record.exists ? 'retrospectiveExisting' : (record.can_create === false ? 'retrospectiveUnavailable' : 'retrospectiveMissing');
        const tone = record.exists ? 'success' : (record.can_create === false ? 'error' : 'neutral');
        renderRetrospective(record, statusKey, tone);
      } catch (error) {
        retrospectiveStatus.hidden = false;
        retrospectiveStatus.dataset.tone = 'error';
        retrospectiveStatus.textContent = error.message;
        retrospectiveContent.textContent = translateTask('retrospectiveNoContent');
        retrospectiveMeta.innerHTML = '';
        retrospectiveCreateTargetButton.hidden = false;
        retrospectiveCreateBranchButton.hidden = false;
      }
    }

    function clearMessages() {
      formError.hidden = true;
      formError.textContent = '';
      document.querySelectorAll('[data-error-for]').forEach((node) => { node.textContent = ''; });
    }

    function normalizeRepoPath(value) {
      return (value || '').toString().trim().replace(/\/+$/, '');
    }

    function deriveRepoContext(path) {
      const normalized = normalizeRepoPath(path);
      const segments = normalized.split('/').filter(Boolean);
      const repoName = segments.length ? segments[segments.length - 1] : 'target repo';
      const parentName = segments.length > 1 ? segments[segments.length - 2] : null;
      return { normalized, repoName, parentName };
    }

    function buildScopeDefaults(path) {
      const context = deriveRepoContext(path);
      const lines = currentUiLanguage() === 'KO'
        ? [
            `코드 변경 범위는 \`${context.normalized}\` 내부로 제한한다.`,
            `이 요청에 필요한 파일만 \`${context.normalized}\` 아래에서 수정한다.`,
            `테스트와 로컬 설정 변경도 \`${context.normalized}\` 범위 안에서만 수행한다.`,
          ]
        : [
            `Limit code changes to \`${context.normalized}\`.`,
            `Modify only the files under \`${context.normalized}\` that are needed for this request.`,
            `Keep tests and local configuration changes scoped to \`${context.normalized}\`.`,
          ];
      if (context.repoName && context.repoName !== 'target repo') lines.push(currentUiLanguage() === 'KO' ? `\`${context.repoName}\` 프로젝트 범위를 우선으로 작업한다.` : `Focus on the \`${context.repoName}\` project or app.`);
      return lines.join('\n');
    }

    function buildOutOfScopeDefaults(path) {
      const context = deriveRepoContext(path);
      const lines = currentUiLanguage() === 'KO'
        ? [
            `\`${context.normalized}\` 밖의 파일은 수정하지 않는다.`,
            `\`${targetRepoDocsRoot}\` 하위 문서는 요청에서 명시하지 않으면 수정하지 않는다.`,
            '관련 없는 앱, 패키지, 워크스페이스 전체 설정은 변경하지 않는다.',
            '요청에서 명시하지 않으면 배포나 인프라 변경은 추가하지 않는다.',
          ]
        : [
            `Do not modify files outside \`${context.normalized}\`.`,
            `Do not modify files under \`${targetRepoDocsRoot}\` unless the request explicitly requires it.`,
            'Do not change unrelated apps, packages, or workspace-wide configuration.',
            'Do not add deployment or infrastructure changes unless the request explicitly asks for them.',
          ];
      if (context.parentName) lines.push(currentUiLanguage() === 'KO' ? `\`${context.parentName}/\` 아래의 다른 프로젝트는 요청에서 명시하지 않으면 수정하지 않는다.` : `Do not modify sibling projects under \`${context.parentName}/\` unless the request explicitly requires it.`);
      return lines.join('\n');
    }

    function buildAcceptanceCriteriaDefaults() {
      return currentUiLanguage() === 'KO'
        ? ['이 요청으로 추가하거나 변경한 코드의 모든 케이스를 테스트해야 하며, 그 변경 범위의 테스트 커버리지는 100%여야 한다.', '저장소 전체 커버리지 100%를 요구하는 뜻은 아니며, 전체 테스트 suite 는 작업 범위와 별개로 수행에 성공해야 한다.'].join('\n')
        : ['Add tests for every case introduced by the code added or changed for this request, and keep test coverage for that changed scope at 100%.', 'This does not require 100% coverage across the entire repository; the full test suite must still pass separately from the changed-scope coverage target.'].join('\n');
    }

    function canReplaceAutofill(field, nextValue, lastValue) {
      return !field.value.trim() || field.dataset.autofilled === 'true' || field.value === lastValue || field.value === nextValue;
    }

    function applyRepoDefaults() {
      const repoPath = normalizeRepoPath(targetRepoInput.value);
      if (!repoPath) return;
      const nextScope = buildScopeDefaults(repoPath);
      const nextOutOfScope = buildOutOfScopeDefaults(repoPath);
      const nextAcceptanceCriteria = buildAcceptanceCriteriaDefaults();
      if (canReplaceAutofill(scopeField, nextScope, lastAutoScope)) {
        scopeField.value = nextScope;
        scopeField.dataset.autofilled = 'true';
      }
      if (canReplaceAutofill(outOfScopeField, nextOutOfScope, lastAutoOutOfScope)) {
        outOfScopeField.value = nextOutOfScope;
        outOfScopeField.dataset.autofilled = 'true';
      }
      if (canReplaceAutofill(acceptanceCriteriaField, nextAcceptanceCriteria, lastAutoAcceptanceCriteria)) {
        acceptanceCriteriaField.value = nextAcceptanceCriteria;
        acceptanceCriteriaField.dataset.autofilled = 'true';
      }
      lastAutoScope = nextScope;
      lastAutoOutOfScope = nextOutOfScope;
      lastAutoAcceptanceCriteria = nextAcceptanceCriteria;
    }

    function resetFormState(options = {}) {
      const { clearSavedDraft = true } = options;
      const previousDraftId = requestDraftId;
      invalidateBranchLookup();
      const previousUploadToken = requestUploadToken;
      requestUploadToken = generateRequestUploadToken();
      void cleanupRequestUploads(previousUploadToken);
      requestForm.reset();
      setRequestGoalEditorContent('', { initialize: false });
      targetRepoInput.value = defaultTargetRepo;
      targetRepoInput.dataset.autofilled = defaultTargetRepo ? 'true' : 'false';
      baseBranchInput.value = defaultBaseBranch;
      baseBranchInput.dataset.autofilled = 'true';
      lastAutoBaseBranch = defaultBaseBranch;
      replaceBaseBranchSuggestions([]);
      updateBaseBranchHelp(translateRequest('baseBranchHelp'));
      scopeField.dataset.autofilled = 'true';
      outOfScopeField.dataset.autofilled = 'true';
      acceptanceCriteriaField.dataset.autofilled = 'true';
      applyTargetRepoAutofill(currentTargetRepoOptions());
      applyRepoDefaults();
      resetRequestDraftState();
      setRequestComposerTab('assistant');
      if (clearSavedDraft) {
        requestDraftId = previousDraftId;
        void deleteRequestComposerDraftState({ silent: true });
      }
    }

    function escapeHtml(value) {
      return (value || '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#39;');
    }

    function setTaskTab(tab, { load = true } = {}) {
      const previousTab = activeTaskTab;
      activeTaskTab = tab;
      taskTabOverview.classList.toggle('active', tab === 'overview');
      taskTabLogs.classList.toggle('active', tab === 'logs');
      taskTabChangedFiles.classList.toggle('active', tab === 'changed-files');
      taskTabQaChecklist.classList.toggle('active', tab === 'qa-checklist');
      taskTabReviewerQa.classList.toggle('active', tab === 'reviewer-qa');
      taskTabReviewNote.classList.toggle('active', tab === 'review-note');
      taskTabEditor.classList.toggle('active', tab === 'editor');
      taskPanelOverview.hidden = tab !== 'overview';
      taskPanelLogs.hidden = tab !== 'logs';
      taskPanelChangedFiles.hidden = tab !== 'changed-files';
      taskPanelQaChecklist.hidden = tab !== 'qa-checklist';
      taskPanelReviewerQa.hidden = tab !== 'reviewer-qa';
      taskPanelReviewNote.hidden = tab !== 'review-note';
      taskPanelEditor.hidden = tab !== 'editor';
      if (tab === 'review-note') requestAnimationFrame(() => syncHumanReviewEditorHeight());
      if (tab === 'editor' && planEditMode && activeArtifactName === 'PLAN.md') schedulePlanEditorHeightSync();
      if (tab === 'reviewer-qa' && previousTab !== 'reviewer-qa') {
        requestAnimationFrame(() => scrollReviewerQaTranscriptToBottom());
      }
      updateReviewerQaLiveRefresh();
      if (!load) return;
      if (tab === 'logs' && activeTaskId) loadTaskLogs(activeTaskId);
      if (tab === 'changed-files' && activeTaskId) loadChangedFile(activeTaskId, activeChangedFileId);
      if (tab === 'qa-checklist') renderQaChecklistPanel();
      if (tab === 'reviewer-qa' && activeTaskId) loadTaskDetail(activeTaskId, true, { softRefresh: true, reloadArtifact: false });
      if (tab === 'editor' && activeTaskId) loadMarkdownArtifact(activeTaskId, activeArtifactName);
    }

    function taskChromeState(state = '') {
      return {
        changedFilesVisible: state === 'human-verifying' || state === 'done',
        qaChecklistVisible: state === 'completed-reviews' || state === 'human-verifying',
        reviewerQaVisible: state === 'completed-reviews' || state === 'human-verifying',
        reviewNoteVisible: state === 'human-verifying',
        viewerVisible: true,
        defaultTab: state === 'waiting-check-plans' ? 'editor' : (state === 'human-verifying' ? 'changed-files' : 'overview'),
      };
    }

    function hydrateTaskModalChrome(snapshot = null, { preserveTab = false } = {}) {
      const state = snapshot?.state || '';
      const canResumePlannerFromSnapshot = state === 'requests'
        && typeof snapshot?.metadata?.retry_gate?.reason === 'string'
        && snapshot.metadata.retry_gate.reason.startsWith('planner-');
      const canResumeImplementerFromSnapshot = canResumeImplementerForMetadata(snapshot?.metadata, state);
      const canResumeReviewerFromSnapshot = state === 'waiting-reviews'
        && typeof snapshot?.metadata?.retry_gate?.reason === 'string'
        && snapshot.metadata.retry_gate.reason.startsWith('review-')
        && Boolean(snapshot?.metadata?.retry_gate?.not_before);
      const canResumeReviewLoopFromSnapshot = state === 'todos' && snapshot?.metadata?.review?.human_rework_required === true;
      const chrome = taskChromeState(state);
      const nextTab = preserveTab ? activeTaskTab : chrome.defaultTab;
      document.getElementById('task-modal-title').textContent = snapshot?.title || translateTask('modalTitle');
      document.getElementById('task-modal-subtitle').innerHTML = renderTaskSubtitleTags(snapshot);
      taskTabChangedFiles.hidden = !chrome.changedFilesVisible;
      taskTabQaChecklist.hidden = !chrome.qaChecklistVisible;
      taskTabReviewerQa.hidden = !chrome.reviewerQaVisible;
      taskTabReviewNote.hidden = !chrome.reviewNoteVisible;
      taskTabEditor.hidden = !chrome.viewerVisible;
      togglePlanEditButton.hidden = state !== 'waiting-check-plans';
      togglePlanEditButton.disabled = state !== 'waiting-check-plans';
      savePlanButton.hidden = state !== 'waiting-check-plans';
      savePlanButton.disabled = true;
      approvePlanButton.hidden = state !== 'waiting-check-plans';
      approvePlanButton.disabled = state !== 'waiting-check-plans';
      startVerificationButton.hidden = state !== 'completed-reviews';
      startVerificationButton.disabled = state !== 'completed-reviews';
      retryVerificationApplyButton.hidden = state !== 'human-verifying';
      retryVerificationApplyButton.disabled = state !== 'human-verifying';
      resumePlannerButton.hidden = !canResumePlannerFromSnapshot;
      resumePlannerButton.disabled = !canResumePlannerFromSnapshot;
      resumeImplementerButton.hidden = !canResumeImplementerFromSnapshot;
      resumeImplementerButton.disabled = !canResumeImplementerFromSnapshot;
      resumeReviewerButton.hidden = !canResumeReviewerFromSnapshot;
      resumeReviewerButton.disabled = !canResumeReviewerFromSnapshot;
      resumeReviewLoopButton.hidden = !canResumeReviewLoopFromSnapshot;
      resumeReviewLoopButton.disabled = !canResumeReviewLoopFromSnapshot;
      if (!canResumePlannerFromSnapshot) setResumePlannerChoiceModalOpen(false, { force: true });
      if (!canResumeImplementerFromSnapshot) setResumeImplementerChoiceModalOpen(false, { force: true });
      if (!canResumeReviewerFromSnapshot) setResumeReviewerChoiceModalOpen(false, { force: true });
      requestChangesButton.hidden = state !== 'human-verifying';
      requestChangesShell.hidden = state !== 'human-verifying';
      approveHumanReviewButton.hidden = state !== 'human-verifying';
      approveHumanReviewShell.hidden = state !== 'human-verifying';
      deleteTaskButton.hidden = !snapshot;
      deleteTaskButton.disabled = !snapshot;
      if (state === 'waiting-check-plans' || state === 'plan-approving') activeArtifactName = 'PLAN.md';
      setTaskTab(nextTab, { load: false });
      return nextTab;
    }

    async function ensureChangedFileSummaries(taskId) {
      if (!activeTaskDetail?.changed_files_available) return [];
      if (activeTaskDetail.changed_files.length) return activeTaskDetail.changed_files;
      const response = await fetch(`/api/tasks/${taskId}?include_changed_files=true`);
      const detail = await response.json();
      if (!response.ok) throw new Error(detail.detail || translateTask('failedLoadTaskDetails'));
      if (taskId !== activeTaskId) return [];
      activeTaskDetail = { ...activeTaskDetail, changed_files_available: detail.changed_files_available, changed_files: detail.changed_files };
      if (!activeChangedFileId || !activeTaskDetail.changed_files.some((file) => file.id === activeChangedFileId)) {
        activeChangedFileId = activeTaskDetail.changed_files[0]?.id || null;
      }
      renderChangedFileButtons(activeTaskDetail.changed_files);
      if (!activeTaskDetail.changed_files.length) renderDiffPlaceholder(translateTask('changedFilesPatchAvailable'));
      return activeTaskDetail.changed_files;
    }

    function renderTaskActivity(detail) {
      const metadata = detail?.metadata || {};
      const lease = metadata.lease || {};
      const status = resolveAgentActivity({
        status: detail?.agent_status,
        owner: lease.owner,
        heartbeatAt: lease.heartbeat_at,
        state: metadata.state,
      });
      const owner = lease.owner || translateTask('noLeaseOwner');
      const runId = lease.run_id || translateTask('noActiveRun');
      const heartbeat = lease.heartbeat_at ? formatDateTime(lease.heartbeat_at) : translateTask('noHeartbeatYet');
      const heartbeatNote = lease.heartbeat_at ? translateTask('heartbeatLast', { time: formatRelativeTime(lease.heartbeat_at) }) : translateTask('heartbeatAppearsLater');
      const activityCopy = status === 'active'
        ? translateTask('activityCopyActive')
        : status === 'waiting'
          ? translateTask('activityCopyWaiting')
          : translateTask('activityCopyIdle');
      const logSummary = detail.log_files.length
        ? translateTask(detail.log_files.length === 1 ? 'runtimeLogSummaryOne' : 'runtimeLogSummaryMany', { count: detail.log_files.length, latest: detail.log_files[detail.log_files.length - 1] })
        : translateTask('runtimeLogSummaryEmpty');
      return `
        <div class="task-section">
          <h3>${escapeHtml(translateTask('taskActivity'))}</h3>
          <div class="task-activity-shell">
            <div class="task-activity-hero">
              <div class="task-activity-copy">
                ${buildActivityBadge(status)}
                <strong>${escapeHtml(owner)}</strong>
                <p>${escapeHtml(activityCopy)}</p>
              </div>
            </div>
            <div class="task-activity-grid">
              <div class="task-activity-card">
                <span>${escapeHtml(translateTask('leaseOwner'))}</span>
                <strong>${escapeHtml(owner)}</strong>
                <small>${escapeHtml(lease.owner ? translateTask('leaseOwnerHelpActive') : translateTask('leaseOwnerHelpEmpty'))}</small>
              </div>
              <div class="task-activity-card">
                <span>${escapeHtml(translateTask('leaseRun'))}</span>
                <strong>${escapeHtml(runId)}</strong>
                <small>${escapeHtml(lease.run_id ? translateTask('leaseRunHelpActive') : translateTask('leaseRunHelpEmpty'))}</small>
              </div>
              <div class="task-activity-card">
                <span>${escapeHtml(translateTask('heartbeat'))}</span>
                <strong>${escapeHtml(heartbeat)}</strong>
                <small>${escapeHtml(heartbeatNote)}</small>
              </div>
              <div class="task-activity-card">
                <span>${escapeHtml(translateTask('logArtifacts'))}</span>
                <strong>${escapeHtml(detail.log_files.length ? detail.log_files.join(', ') : translateTask('noneYet'))}</strong>
                <small>${escapeHtml(logSummary)}</small>
              </div>
            </div>
          </div>
        </div>`;
    }

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

    function renderQaChecklistPanel() {
      const state = activeTaskDetail?.metadata?.state || '';
      const canToggle = state === 'human-verifying' && !taskDetailStale;
      const items = qaChecklistItems();
      const progress = qaChecklistProgress();
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
                  <span class="diff-badge">${escapeHtml(item.required ? translateHumanReview('qaChecklistRequired') : translateHumanReview('qaChecklistOptional'))}</span>
                  <span class="diff-badge">${escapeHtml(item.id)}</span>
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
    }

    function applyQaChecklistItemUpdate(updated) {
      if (!updated || !activeTaskDetail?.human_review?.qa_items) return;
      activeTaskDetail.human_review.qa_items = activeTaskDetail.human_review.qa_items.map((item) => item.id === updated.id ? { ...item, ...updated } : item);
      const progress = qaChecklistProgress();
      activeTaskDetail.human_review.qa_total_count = progress.total;
      activeTaskDetail.human_review.qa_required_count = progress.required;
      activeTaskDetail.human_review.qa_completed_required_count = progress.completedRequired;
      renderQaChecklistPanel();
      updateHumanReviewPanel();
    }

    async function setQaChecklistItemState(taskId, itemId, patch) {
      const response = await fetch(`/api/tasks/${taskId}/human-qa/${encodeURIComponent(itemId)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || translateHumanReview('qaChecklistSaveError'));
      applyQaChecklistItemUpdate(payload);
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

    async function sendRequestDraftMessage() {
      const message = (requestDraftInput.value || '').trim();
      if (!message || requestDraftMessageInFlight) {
        updateRequestDraftPanel();
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
