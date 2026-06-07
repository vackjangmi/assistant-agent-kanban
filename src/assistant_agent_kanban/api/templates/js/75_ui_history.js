    const uiBoardPath = '/board';
    const uiBoardPhases = new Set(['plan', 'implementation', 'final', 'closed', 'archive']);
    const uiTaskTabs = new Set(['overview', 'inspector', 'logs', 'changed-files', 'qa-checklist', 'reviewer-qa', 'review-note', 'editor']);
    const uiSettingsTabs = new Set(['general', 'git', 'repositories', 'roles', 'slack', 'slack-channel', 'users']);
    const uiComposerTabs = new Set(['assistant', 'fields']);
    let uiRouteApplying = false;

    function normalizedUiPath(pathname = window.location.pathname) {
      const trimmed = pathname.replace(/\/+$/, '');
      return trimmed || '/';
    }

    function uiPathSegments(pathname = window.location.pathname) {
      return normalizedUiPath(pathname)
        .split('/')
        .filter(Boolean)
        .map((segment) => {
          try {
            return decodeURIComponent(segment);
          } catch (_error) {
            return segment;
          }
        });
    }

    function encodeUiSegment(value) {
      return encodeURIComponent(value || '');
    }

    function boardRoutePath(phase = activeBoardPhase || 'plan') {
      const normalizedPhase = uiBoardPhases.has(phase) ? phase : 'plan';
      return `${uiBoardPath}/${normalizedPhase}`;
    }

    function taskRoutePath(taskId = activeTaskId, tab = activeTaskTab) {
      if (!taskId) return boardRoutePath();
      const taskPath = `/tasks/${encodeUiSegment(taskId)}`;
      return uiTaskTabs.has(tab) ? `${taskPath}/${tab}` : taskPath;
    }

    function settingsRoutePath(tab = activeSettingsTab || 'general') {
      const normalizedTab = uiSettingsTabs.has(tab) ? tab : 'general';
      return `/settings/${normalizedTab}`;
    }

    function requestComposerRoutePath(tab = activeRequestComposerTab || 'assistant') {
      const normalizedTab = uiComposerTabs.has(tab) ? tab : 'assistant';
      return `/requests/new/${normalizedTab}`;
    }

    function currentUiRoutePath() {
      if (!taskModal.hidden && activeTaskId) return taskRoutePath(activeTaskId, activeTaskTab);
      if (!settingsModal.hidden) return settingsRoutePath(activeSettingsTab);
      if (!modal.hidden) return requestComposerRoutePath(activeRequestComposerTab);
      return boardRoutePath(activeBoardPhase);
    }

    function writeUiRoute(path, { replace = false } = {}) {
      const normalizedPath = normalizedUiPath(path);
      const currentPath = normalizedUiPath();
      const state = { assistantAgentKanbanRoute: normalizedPath };
      if (replace || normalizedPath === currentPath) {
        window.history.replaceState(state, '', normalizedPath);
        return;
      }
      window.history.pushState(state, '', normalizedPath);
    }

    function syncCurrentUiRoute({ replace = true } = {}) {
      writeUiRoute(currentUiRoutePath(), { replace });
    }

    async function closeNavigableViewsForRoute(target) {
      if (target !== 'request' && !modal.hidden) {
        clearMessages();
        void syncRequestComposerDraftState({ immediate: true, silent: true });
        setModalOpen(false);
      }
      if (target !== 'settings' && !settingsModal.hidden) {
        const closed = closeSettingsModal({ restore: true });
        if (!closed) {
          syncCurrentUiRoute({ replace: true });
          return false;
        }
      }
      if (target !== 'task' && !taskModal.hidden) setTaskModalOpen(false);
      if (!retrospectiveModal.hidden) setRetrospectiveModalOpen(false);
      if (!approvalChoiceModal.hidden) setApprovalChoiceModalOpen(false, { force: true });
      if (!dirtyTargetConfirmationModal.hidden) setDirtyTargetConfirmationModalOpen(false, { force: true });
      if (!resumePlannerChoiceModal.hidden) setResumePlannerChoiceModalOpen(false, { force: true });
      if (!resumeImplementerChoiceModal.hidden) setResumeImplementerChoiceModalOpen(false, { force: true });
      if (!resumeReviewerChoiceModal.hidden) setResumeReviewerChoiceModalOpen(false, { force: true });
      if (directoryPickerModal && !directoryPickerModal.hidden) setDirectoryPickerModalOpen(false);
      return true;
    }

    async function openRequestComposerFromRoute(tab = 'assistant') {
      const normalizedTab = uiComposerTabs.has(tab) ? tab : 'assistant';
      if (!await closeNavigableViewsForRoute('request')) return;
      if (modal.hidden) {
        clearMessages();
        applyRequestTranslations();
        if (!await restoreRequestComposerDraftState()) resetFormState({ clearSavedDraft: false });
        setModalOpen(true);
        await loadTargetRepoBranches();
      }
      setRequestComposerTab(normalizedTab);
    }

    async function openSettingsFromRoute(tab = 'general') {
      const normalizedTab = uiSettingsTabs.has(tab) ? tab : 'general';
      if (!await closeNavigableViewsForRoute('settings')) return;
      await openSettingsModal();
      if (!setSettingsTab(normalizedTab)) {
        syncCurrentUiRoute({ replace: true });
        return;
      }
      if (normalizedTab === 'users') {
        loadUsers().catch((error) => setCreateUserStatus(error.message, 'error'));
      }
    }

    async function openTaskFromRoute(taskId, tab = '') {
      if (!taskId) {
        await openBoardFromRoute('plan');
        return;
      }
      if (!await closeNavigableViewsForRoute('task')) return;
      if (uiTaskTabs.has(tab)) {
        activeTaskTabUserSelectionVersion += 1;
        setTaskTab(tab, { load: false });
        await loadTaskDetail(taskId, true, { snapshot: boardTaskSnapshots.get(taskId) || null });
        return;
      }
      await loadTaskDetail(taskId, false, { snapshot: boardTaskSnapshots.get(taskId) || null });
    }

    async function openBoardFromRoute(phase = 'plan') {
      const normalizedPhase = uiBoardPhases.has(phase) ? phase : 'plan';
      if (!await closeNavigableViewsForRoute('board')) return;
      activeArchiveGroup = null;
      activeBoardPhase = normalizedPhase;
      boardPhaseManuallySelected = true;
      renderBoardPhaseTabs();
      if (normalizedPhase === 'archive') {
        if (activeBoardSnapshot) {
          applyBoardSnapshot(activeBoardSnapshot);
        } else {
          board.classList.add('archive-board');
          board.innerHTML = renderArchiveBoard();
          await loadArchives();
        }
        return;
      }
      await loadBoard();
    }

    async function applyUiRouteFromLocation() {
      const segments = uiPathSegments();
      const [section, first, second] = segments;
      uiRouteApplying = true;
      try {
        if (!section) {
          await openBoardFromRoute('plan');
          return;
        }
        if (section === 'board') {
          await openBoardFromRoute(first || 'plan');
          return;
        }
        if (section === 'tasks') {
          await openTaskFromRoute(first || '', second || '');
          return;
        }
        if (section === 'settings') {
          await openSettingsFromRoute(first || 'general');
          return;
        }
        if (section === 'requests' && first === 'new') {
          await openRequestComposerFromRoute(second || 'assistant');
          return;
        }
        await openBoardFromRoute('plan');
        syncCurrentUiRoute({ replace: true });
      } finally {
        uiRouteApplying = false;
      }
    }

    function navigateToUiPath(path, options = {}) {
      if (uiRouteApplying) return;
      writeUiRoute(path, options);
      applyUiRouteFromLocation().catch((error) => {
        console.error(error);
      });
    }

    function navigateToBoardPhase(phase = activeBoardPhase || 'plan', options = {}) {
      navigateToUiPath(boardRoutePath(phase), options);
    }

    function navigateToTask(taskId, tab = '', options = {}) {
      if (!taskId) return;
      navigateToUiPath(uiTaskTabs.has(tab) ? taskRoutePath(taskId, tab) : `/tasks/${encodeUiSegment(taskId)}`, options);
    }

    function navigateToTaskTab(tab, options = {}) {
      if (!activeTaskId || !uiTaskTabs.has(tab)) return;
      navigateToUiPath(taskRoutePath(activeTaskId, tab), options);
    }

    function navigateToSettingsTab(tab = 'general', options = {}) {
      navigateToUiPath(settingsRoutePath(tab), options);
    }

    function navigateToRequestComposerTab(tab = 'assistant', options = {}) {
      navigateToUiPath(requestComposerRoutePath(tab), options);
    }

    function initializeUiRouting() {
      window.history.replaceState({ assistantAgentKanbanRoute: normalizedUiPath() }, '', normalizedUiPath());
      window.addEventListener('popstate', () => {
        applyUiRouteFromLocation().catch((error) => {
          console.error(error);
        });
      });
      return applyUiRouteFromLocation();
    }
