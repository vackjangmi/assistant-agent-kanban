    function inspectionHealthLabel(health = '') {
      const labels = {
        active: translateTask('inspectionHealthActive'),
        stale: translateTask('inspectionHealthStale'),
        waiting: translateTask('inspectionHealthWaiting'),
        blocked: translateTask('inspectionHealthBlocked'),
        idle: translateTask('inspectionHealthIdle'),
      };
      return labels[health] || health || translateTask('unknown');
    }

    function inspectionSummaryText(inspection) {
      const health = inspection?.health || '';
      const heartbeatAge = Number(inspection?.lease_age_seconds);
      const logAge = Number(inspection?.last_log_age_seconds);
      const staleAfter = Number(inspection?.stale_after_seconds);
      const hasRecentLog = Number.isFinite(logAge) && Number.isFinite(staleAfter) && logAge <= staleAfter;
      if (health === 'stale' && hasRecentLog && Number.isFinite(heartbeatAge) && heartbeatAge > staleAfter) {
        return translateTask('inspectionSummaryLogActiveHeartbeatStale', {
          heartbeat: Number.isFinite(heartbeatAge) ? heartbeatAge : translateTask('unknown'),
          log: logAge,
        });
      }
      const keys = {
        active: 'inspectionSummaryActive',
        stale: 'inspectionSummaryStale',
        waiting: 'inspectionSummaryWaiting',
        blocked: 'inspectionSummaryBlocked',
        idle: 'inspectionSummaryIdle',
      };
      const translated = keys[health] ? translateTask(keys[health]) : '';
      return translated || inspection?.summary || translateTask('inspectorIdle');
    }

    function inspectorFaqLabel(faq) {
      const keys = {
        'is-running': 'inspectorFaqIsRunning',
        'latest-activity': 'inspectorFaqLatestActivity',
        'why-waiting': 'inspectorFaqWhyWaiting',
        'workspace-changes': 'inspectorFaqWorkspaceChanges',
        'next-step': 'inspectorFaqNextStep',
      };
      const key = keys[faq?.id || ''];
      return (key ? translateTask(key) : '') || faq?.label || faq?.question || '';
    }

    function inspectionSignalLabel(signal) {
      const keys = {
        Health: 'inspectorSignalHealth',
        Heartbeat: 'inspectorSignalHeartbeat',
        'Latest log': 'inspectorSignalLatestLog',
        Workspace: 'inspectorSignalWorkspace',
        'Retry gate': 'inspectorSignalRetryGate',
        'Recent errors': 'inspectorSignalRecentErrors',
      };
      return translateTask(keys[signal?.label] || '') || signal?.label || '';
    }

    function inspectionSignalValue(signal) {
      const value = signal?.value || '';
      if (signal?.label === 'Health') return inspectionHealthLabel(value);
      if (value === 'none') return translateTask('inspectorSignalNone');
      const workspaceMatch = signal?.label === 'Workspace' ? value.match(/^(\d+) changed paths$/) : null;
      if (workspaceMatch) return translateTask('inspectorWorkspaceChangedCount', { count: workspaceMatch[1] });
      return value;
    }

    function inspectionSignalDetail(signal, inspection) {
      if (signal?.label === 'Health') return inspectionSummaryText({ health: signal.value, summary: signal.detail });
      if (signal?.label === 'Heartbeat') return translateTask('inspectorSignalHeartbeatDetail', { seconds: inspection?.stale_after_seconds || '' });
      if (signal?.label === 'Latest log') return translateTask('inspectorSignalLatestLogDetail');
      if (signal?.label === 'Workspace') return translateTask('inspectorSignalWorkspaceDetail');
      if (signal?.label === 'Retry gate') {
        return translateTask(signal.tone === 'warning' ? 'inspectorSignalRetryGatePausedDetail' : 'inspectorSignalRetryGateRecordedDetail');
      }
      return signal?.detail || '';
    }

    function renderInspectionSignals(inspection) {
      const signals = Array.isArray(inspection?.signals) ? inspection.signals : [];
      if (!signals.length) {
        taskInspectorSignals.innerHTML = `<div class="task-inspector-signal"><span>${escapeHtml(translateTask('inspectorNoSignals'))}</span></div>`;
        return;
      }
      taskInspectorSignals.innerHTML = signals.map((signal) => `
        <div class="task-inspector-signal" data-tone="${escapeHtml(signal.tone || 'neutral')}">
          <span>${escapeHtml(inspectionSignalLabel(signal))}</span>
          <strong>${escapeHtml(inspectionSignalValue(signal))}</strong>
          <small>${escapeHtml(inspectionSignalDetail(signal, inspection))}</small>
        </div>
      `).join('');
    }

    function renderInspectorFaqs(inspection) {
      const faqs = Array.isArray(inspection?.faqs) ? inspection.faqs : [];
      taskInspectorFaqButtons.innerHTML = faqs.map((faq) => `
        <button type="button" class="task-inspector-faq-button" data-inspector-faq="${escapeHtml(faq.id || '')}">
          ${escapeHtml(inspectorFaqLabel(faq))}
        </button>
      `).join('');
    }

    function renderInspectorAnswer({ question = '', answer = '', meta = '', tone = '' } = {}) {
      if (!question && !answer) {
        taskInspectorAnswer.innerHTML = `<p class="task-inspector-empty">${escapeHtml(translateTask('inspectorEmpty'))}</p>`;
        return;
      }
      taskInspectorAnswer.innerHTML = `
        ${question ? `<div class="reviewer-qa-entry" data-side="current">
          <div class="reviewer-qa-shell">
            <div class="reviewer-qa-meta">
              <span class="reviewer-qa-role">${escapeHtml(translateTask('inspectorQuestion'))}</span>
            </div>
            <div class="reviewer-qa-bubble">${escapeHtml(question)}</div>
          </div>
        </div>` : ''}
        ${answer ? `<div class="reviewer-qa-entry" data-side="system" ${tone ? `data-tone="${escapeHtml(tone)}"` : ''}>
          <div class="reviewer-qa-shell">
            <div class="reviewer-qa-meta">
              <span class="reviewer-qa-role">${escapeHtml(translateTask('taskInspector'))}</span>
              ${meta ? `<span class="transcript-live-badge">${escapeHtml(meta)}</span>` : ''}
            </div>
            <div class="reviewer-qa-bubble">${escapeHtml(answer)}</div>
          </div>
        </div>` : ''}
      `;
      taskInspectorAnswer.scrollTop = taskInspectorAnswer.scrollHeight;
    }

    function setActiveInspectorFaq(questionId) {
      taskInspectorFaqButtons.querySelectorAll('[data-inspector-faq]').forEach((button) => {
        button.classList.toggle('active', Boolean(questionId) && button.dataset.inspectorFaq === questionId);
      });
    }

    function updateTaskInspectorPanel() {
      const question = (taskInspectorInput.value || '').trim();
      askTaskInspectorButton.disabled = taskInspectionQuestionInFlight || !activeTaskId || !question;
      refreshTaskInspectionButton.disabled = taskInspectionQuestionInFlight || !activeTaskId;
    }

    function renderTaskInspection(inspection) {
      activeTaskInspection = inspection;
      const health = inspection?.health || 'idle';
      taskInspectorStatus.innerHTML = `
        <span class="inspection-health-pill" data-health="${escapeHtml(health)}">${escapeHtml(inspectionHealthLabel(health))}</span>
        <span>${escapeHtml(inspectionSummaryText(inspection))}</span>
      `;
      renderInspectionSignals(inspection);
      renderInspectorFaqs(inspection);
      updateTaskInspectorPanel();
    }

    async function loadTaskInspection(taskId, { force = false } = {}) {
      if (!taskId) return;
      if (activeTaskInspection && !force && activeTaskInspection.task_id === taskId) {
        renderTaskInspection(activeTaskInspection);
        return;
      }
      const requestToken = ++activeTaskInspectionRequestToken;
      taskInspectorStatus.textContent = translateTask('inspectorLoading');
      refreshTaskInspectionButton.disabled = true;
      const response = await fetch(`/api/tasks/${taskId}/inspection`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || translateTask('inspectorLoadFailed'));
      if (requestToken !== activeTaskInspectionRequestToken || taskId !== activeTaskId) return;
      renderTaskInspection(payload);
    }

    async function askTaskInspector(questionId = null) {
      if (!activeTaskId) return;
      const question = questionId ? '' : (taskInspectorInput.value || '').trim();
      if (!question && !questionId) return;
      const displayQuestion = questionId ? inspectorFaqLabel({ id: questionId }) : question;
      taskInspectionQuestionInFlight = true;
      setActiveInspectorFaq(questionId);
      updateTaskInspectorPanel();
      taskInspectorStatus.textContent = translateTask('inspectorAsking');
      renderInspectorAnswer({
        question: displayQuestion,
        answer: translateTask('inspectorAsking'),
        meta: translateTask('inspectorPending'),
        tone: 'pending',
      });
      try {
        const response = await fetch(`/api/tasks/${activeTaskId}/inspection/questions`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ question, question_id: questionId }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.detail || translateTask('inspectorAskFailed'));
        if (payload.inspection) renderTaskInspection(payload.inspection);
        const meta = payload.resolved_model ? payload.resolved_model : '';
        const resolvedDisplayQuestion = questionId
          ? inspectorFaqLabel({ id: questionId, question: payload.question || '' })
          : (payload.question || question);
        renderInspectorAnswer({ question: resolvedDisplayQuestion, answer: payload.answer || '', meta });
        if (!questionId) taskInspectorInput.value = '';
      } catch (error) {
        renderInspectorAnswer({
          question: displayQuestion,
          answer: error.message || translateTask('inspectorAskFailed'),
          meta: translateTask('inspectorFailed'),
          tone: 'error',
        });
        throw error;
      } finally {
        taskInspectionQuestionInFlight = false;
        updateTaskInspectorPanel();
      }
    }

    taskInspectorFaqButtons.addEventListener('click', (event) => {
      const button = event.target.closest('[data-inspector-faq]');
      if (!button || taskInspectionQuestionInFlight) return;
      askTaskInspector(button.dataset.inspectorFaq || '').catch((error) => {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
        updateTaskInspectorPanel();
      });
    });
