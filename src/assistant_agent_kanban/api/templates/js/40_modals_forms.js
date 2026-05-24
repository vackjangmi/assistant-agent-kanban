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

    function normalizePath(p) {
      if (!p) return '';
      return p.replace(/\\/g, '/').replace(/\/$/, '');
    }

    function getRelativeDepth(root, current) {
      const r = normalizePath(root);
      const c = normalizePath(current);
      if (r === c) return 0;
      if (!c.startsWith(r + '/')) return -1;
      const relative = c.substring(r.length + 1);
      return relative.split('/').filter(Boolean).length;
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
      if (isOpen) {
        setSettingsTab('general');
        if (runtimeLanguageInput) {
          runtimeLanguageInput.focus();
        }
      }
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
      runtimeLanguageInput.dispatchEvent(new Event('change'));
      runtimeThemeInput.value = data.theme || 'light';
      applyRuntimeTheme(runtimeThemeInput.value);
      cachedAssistantOptions = resolveAssistantOptions(data);
      lastSettingsPayload = data;
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
    window.openSettingsModal = openSettingsModal;

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
        runtimeLanguageInput.dispatchEvent(new Event('change'));
        runtimeThemeInput.value = data.theme || 'light';
        applyRuntimeTheme(runtimeThemeInput.value);
        cachedAssistantOptions = resolveAssistantOptions(data);
        lastSettingsPayload = data;
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
        void loadTargetRepoOptions().catch(() => {});
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
      body.classList.toggle('modal-open', !modal.hidden || !settingsModal.hidden || !taskModal.hidden || !retrospectiveModal.hidden || !approvalChoiceModal.hidden || !resumeImplementerChoiceModal.hidden || !resumeReviewerChoiceModal.hidden || !directoryPickerModal.hidden);
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
      targetRepoInput.value = '';
      targetRepoInput.dataset.autofilled = 'false';
      baseBranchInput.value = defaultBaseBranch;
      baseBranchInput.dataset.autofilled = 'true';
      lastAutoBaseBranch = defaultBaseBranch;
      replaceBaseBranchSuggestions([]);
      updateBaseBranchHelp(translateRequest('baseBranchHelp'));
      scopeField.dataset.autofilled = 'true';
      outOfScopeField.dataset.autofilled = 'true';
      acceptanceCriteriaField.dataset.autofilled = 'true';
      applyRepoDefaults();
      resetRequestDraftState();
      setRequestComposerTab('assistant');
      if (clearSavedDraft) {
        requestDraftId = previousDraftId;
        void deleteRequestComposerDraftState({ silent: true });
      }
    }

    async function openComposerWithRepo(repoPath) {
      clearMessages();
      applyRequestTranslations();

      const raw = window.localStorage.getItem(requestComposerDraftStorageKey);
      let hasExistingDraftContent = false;
      if (raw) {
        try {
          const saved = JSON.parse(raw);
          const draftId = (saved?.request_draft_id || '').trim();
          if (draftId) {
            const response = await fetch(`/api/request-drafts/${encodeURIComponent(draftId)}`);
            if (response.ok) {
              const draftData = await response.json();
              const hasContent = requestComposerDraftHasContent(draftData);
              const isDifferentRepo = draftData.target_repo && normalizeRepoPath(draftData.target_repo) !== normalizeRepoPath(repoPath);
              if (hasContent || isDifferentRepo) {
                hasExistingDraftContent = true;
              }
            }
          }
        } catch (e) {
          console.error(e);
        }
      }

      if (hasExistingDraftContent) {
        const confirmMsg = currentUiLanguage() === 'KO'
          ? '현재 작성 중인 임시 요청서가 존재합니다. 이를 지우고 선택한 저장소 기준으로 새 요청을 작성하시겠습니까?'
          : 'There is an active draft in progress. Would you like to discard it and start a new request for this repository?';
        if (!window.confirm(confirmMsg)) return;
      }

      resetFormState({ clearSavedDraft: true });
      targetRepoInput.value = repoPath;
      targetRepoInput.dataset.autofilled = 'true';
      applyRepoDefaults();
      setModalOpen(true);
      await loadTargetRepoBranches();
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
        changedFilesVisible: state === 'human-verifying',
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
      splitPlanButton.hidden = !(state === 'waiting-check-plans' && taskHasSplitProposal(snapshot?.metadata));
      splitPlanButton.disabled = splitPlanButton.hidden;
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
      cancelTaskButton.hidden = !snapshot || state === 'done' || state === 'closed';
      cancelTaskButton.disabled = cancelTaskButton.hidden;
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

    function setDirectoryPickerModalOpen(isOpen) {
      if (directoryPickerModal) {
        directoryPickerModal.hidden = !isOpen;
        directoryPickerModal.setAttribute('aria-hidden', String(!isOpen));
        syncBodyModalState();
      }
    }

    async function openDirectoryPicker(targetInput = 'repo_discovery_root') {
      setDirectoryPickerModalOpen(true);
      const inputEl = targetInput === 'target_repo' ? targetRepoInput : repoDiscoveryRootInput;
      directoryPickerTargetInput = inputEl;
      if (targetInput === 'target_repo' && !cachedResolvedRepoDiscoveryRoot) {
        await loadTargetRepoOptions().catch(() => {});
      }
      let path = inputEl ? inputEl.value : '';
      if (targetInput === 'target_repo') {
        const initialDepth = getRelativeDepth(cachedResolvedRepoDiscoveryRoot, path);
        if (!path || path.trim() === '' || initialDepth === -1) {
          path = cachedResolvedRepoDiscoveryRoot;
        }
      }
      await loadPickerDirectory(path);
    }

    async function loadPickerDirectory(path) {
      if (directoryPickerStatus) {
        directoryPickerStatus.hidden = false;
        directoryPickerStatus.dataset.tone = 'neutral';
        directoryPickerStatus.textContent = translateSettings('dirPickerLoading') || 'Loading folders...';
      }
      if (directoryPickerList) {
        directoryPickerList.innerHTML = '';
      }

      try {
        const url = new URL('/api/browse-directories', window.location.origin);
        if (path) {
          url.searchParams.set('path', path);
        }
        const response = await fetch(url);
        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.detail || 'Failed to fetch directories');
        }

        if (data.error) {
          throw new Error(data.error);
        }

        activeDirectoryPickerPath = data.current_path;
        if (directoryPickerCurrentPathDisplay) {
          directoryPickerCurrentPathDisplay.value = data.current_path;
        }

        if (directoryPickerStatus) {
          directoryPickerStatus.hidden = true;
        }

        const relativeDepth = getRelativeDepth(cachedResolvedRepoDiscoveryRoot, data.current_path);
        const isTargetRepoPicker = directoryPickerTargetInput === targetRepoInput;

        let html = '';
        const shouldShowParent = !isTargetRepoPicker || relativeDepth > 0;
        if (data.parent_path && shouldShowParent) {
          const upText = translateSettings('dirPickerUp') || '📁 Up one level (..)';
          html += `<div class="directory-picker-item parent-dir" data-path="${escapeHtml(data.parent_path)}">
            <span class="dir-icon">📁</span>
            <span class="dir-name">${escapeHtml(upText)}</span>
          </div>`;
        }

        const shouldShowSubdirs = !isTargetRepoPicker || relativeDepth < cachedRepoDiscoveryMaxDepth;
        if (data.directories && data.directories.length > 0 && shouldShowSubdirs) {
          data.directories.forEach(dir => {
            html += `<div class="directory-picker-item" data-path="${escapeHtml(dir.path)}">
              <span class="dir-icon">📁</span>
              <span class="dir-name">${escapeHtml(dir.name)}</span>
            </div>`;
          });
        }

        if (directoryPickerList) {
          directoryPickerList.innerHTML = html;
        }

        if (btnDirectoryPickerSelect) {
          if (isTargetRepoPicker) {
            const isValidDepth = relativeDepth >= 1 && relativeDepth <= cachedRepoDiscoveryMaxDepth;
            btnDirectoryPickerSelect.disabled = !isValidDepth;
          } else {
            btnDirectoryPickerSelect.disabled = false;
          }
        }
      } catch (error) {
        if (directoryPickerStatus) {
          directoryPickerStatus.hidden = false;
          directoryPickerStatus.dataset.tone = 'error';
          const errorPattern = translateSettings('dirPickerError') || 'Could not load folder contents: {error}';
          directoryPickerStatus.textContent = errorPattern.replace('{error}', error.message);
        }
      }
    }

    function selectDirectoryPickerCurrent() {
      if (activeDirectoryPickerPath && directoryPickerTargetInput) {
        if (directoryPickerTargetInput === targetRepoInput) {
          const relativeDepth = getRelativeDepth(cachedResolvedRepoDiscoveryRoot, activeDirectoryPickerPath);
          if (relativeDepth < 1 || relativeDepth > cachedRepoDiscoveryMaxDepth) {
            return;
          }
        }
        directoryPickerTargetInput.value = activeDirectoryPickerPath;
        directoryPickerTargetInput.dispatchEvent(new Event('input', { bubbles: true }));
        directoryPickerTargetInput.dispatchEvent(new Event('change', { bubbles: true }));
      }
      setDirectoryPickerModalOpen(false);
    }
