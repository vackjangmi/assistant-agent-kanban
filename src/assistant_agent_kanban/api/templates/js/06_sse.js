    const source = new EventSource('/api/events');
    source.addEventListener('board_snapshot', (event) => {
      const message = JSON.parse(event.data);
      applyBoardSnapshot(message.payload);
    });
    source.addEventListener('task_moved', async (event) => {
      const payload = JSON.parse(event.data);
      if (taskModal.hidden || activeTaskId !== payload.task_id) return;
      scheduleActiveTaskRefresh({ reloadArtifact: true });
    });
    source.addEventListener('worker_log', (event) => {
      if (taskModal.hidden) return;
      const payload = JSON.parse(event.data);
      if (activeTaskId !== payload.task_id) return;
      const reviewerQaUpdated = appendReviewerQaWorkerLogPayload(payload);
      if (activeTaskTab === 'logs') {
        if (appendWorkerLogPayload(payload)) return;
        loadTaskLogs(activeTaskId).catch((error) => {
          taskModalError.hidden = false;
          taskModalError.textContent = error.message;
        });
        return;
      }
      if (reviewerQaUpdated) return;
      scheduleActiveTaskRefresh({ reloadArtifact: false });
    });
    source.addEventListener('worker_log_file', (event) => {
      if (taskModal.hidden) return;
      const payload = JSON.parse(event.data);
      if (activeTaskId !== payload.task_id || activeTaskTab !== 'logs') return;
      if (appendWorkerLogFilePayload(payload)) return;
      loadTaskLogs(activeTaskId).catch((error) => {
        taskModalError.hidden = false;
        taskModalError.textContent = error.message;
      });
    });
    loadBoard();
