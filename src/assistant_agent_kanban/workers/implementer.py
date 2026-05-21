from __future__ import annotations

import asyncio
from pathlib import Path

from ..assistant_adapter import AssistantAdapter
from ..exceptions import AdapterRunError
from ..enums import TaskState
from ..exceptions import WorkspaceSyncError
from ..language import generation_language_name
from ..repo_branches import describe_target_repo_head_drift, snapshot_target_repo_state
from ..models import RunResult, TaskErrorInfo
from ..retry_policy import apply_retry_gate, can_auto_dispatch, clear_retry_gate
from ..workspace_manager import WorkspaceManager
from .base import WorkerBase


class ImplementerWorker(WorkerBase):
    worker_name = "implementer"

    def __init__(self, *args, adapter: AssistantAdapter, workspace_manager: WorkspaceManager, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.adapter = adapter
        self.workspace_manager = workspace_manager

    def candidate_tasks(self):
        return [
            task
            for task in self.scanner.scan()
            if task.state == TaskState.TODOS and can_auto_dispatch(task.metadata)
        ]

    async def run_once(self) -> bool:
        tasks = self.candidate_tasks()
        if not tasks:
            return False
        return await self.run_task(tasks[0])

    async def run_task(self, task) -> bool:
        run_id = self.make_run_id()
        with self.locks.acquire(task.task_dir, task.metadata, owner=self.worker_name, run_id=run_id):
            implementing = self.transitions.move(task, TaskState.IMPLEMENTING, by=self.worker_name)
            await self.emit("task_moved", implementing.metadata.task_id, state=implementing.state.value)
            try:
                workspace_repo = await self._prepare_workspace(implementing)
                self._capture_target_repo_baseline(implementing.metadata)
                self.metadata_store.save(implementing.task_dir, implementing.metadata)
            except WorkspaceSyncError as exc:
                apply_retry_gate(implementing.metadata, reason="implementation-base-sync-conflict")
                implementing.metadata.implementation.last_result = "failure"
                implementing.metadata.errors.append(
                    TaskErrorInfo(code="implementation-base-sync-conflict", message=str(exc))
                )
                self.metadata_store.save(implementing.task_dir, implementing.metadata)
                done = self.transitions.move(implementing, TaskState.TODOS, by=self.worker_name, note="workspace preparation failed")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True
            except AdapterRunError as exc:
                apply_retry_gate(implementing.metadata, reason="implementation-target-baseline-failed")
                implementing.metadata.implementation.last_result = "failure"
                implementing.metadata.errors.append(
                    TaskErrorInfo(code="implementation-target-baseline-failed", message=str(exc))
                )
                self.metadata_store.save(implementing.task_dir, implementing.metadata)
                done = self.transitions.move(implementing, TaskState.TODOS, by=self.worker_name, note="target repo baseline capture failed")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True

            cycle = implementing.metadata.cycle + 1
            log_path = self.task_log_dir(task.metadata.task_id) / "implementer.jsonl"
            log_name = log_path.name
            handshake_prompt = self._build_handshake_prompt(implementing.metadata)
            live_prompt = self.build_prompt(self._build_implementer_source(implementing.task_dir), implementing.metadata, phase="implementer")
            finalize_prompt = self._build_finalize_prompt(implementing.task_dir, implementing.metadata)
            loop = asyncio.get_running_loop()
            await self.announce_log_file(implementing.metadata.task_id, log_name)
            run_config = self._resolve_implementer_run_config(implementing.task_dir, implementing.metadata)
            adapter = self._resolve_implementer_adapter(run_config)
            session_id = self.reuse_session_id(
                session_id=implementing.metadata.implementation.session_id,
                session_tokens=implementing.metadata.implementation.session_tokens,
                budget=run_config.role_session_token_budget("implementer"),
            )
            prior_session_tokens = implementing.metadata.implementation.session_tokens if session_id else 0

            if not self.worker_live_logs_enabled(run_config):
                self.append_log_marker(log_path=log_path, phase="run", cycle=cycle)
                result = await asyncio.to_thread(
                    adapter.run,
                    agent=run_config.role_agent("implementer"),
                    prompt=live_prompt,
                    cwd=workspace_repo,
                    run_log_path=log_path,
                    config=run_config,
                    include_directories=self._opencode_include_directories(run_config, implementing.metadata, workspace_repo),
                    session_id=session_id,
                    cancel_key=implementing.metadata.task_id,
                    on_log_line=self.make_log_callback(loop, implementing.metadata.task_id, log_name),
                )
                implementing.metadata.implementation.resolved_model = result.resolved_model
                implementing.metadata.implementation.session_id = result.session_id
                implementing.metadata.implementation.last_run_tokens = result.total_tokens
                implementing.metadata.implementation.session_tokens = self.next_session_token_total(
                    reused_session_id=session_id,
                    returned_session_id=result.session_id,
                    prior_session_tokens=prior_session_tokens,
                    run_tokens=self.session_budget_tokens(result),
                )
                implementing.metadata.cycle += 1
                drift_note = self._target_repo_state_drift_note(implementing.metadata)
                if drift_note is not None:
                    implementing.metadata.implementation.last_result = "failure"
                    implementing.metadata.errors.append(
                        TaskErrorInfo(code="implementation-target-repo-drift", message=drift_note)
                    )
                    apply_retry_gate(implementing.metadata, reason="implementation-target-repo-drift")
                    self.metadata_store.save(implementing.task_dir, implementing.metadata)
                    done = self.transitions.move(implementing, TaskState.TODOS, by=self.worker_name, note=drift_note)
                    await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                    return True
                changes = self.workspace_changes(workspace_repo)
                has_changes = bool(changes)
                has_substantive_changes = self._has_allowed_implementation_changes(implementing.task_dir, changes)
                has_local_commits = self.workspace_has_local_commits(workspace_repo, implementing.metadata.target.base_branch)
                success = result.ok and has_changes and has_substantive_changes and not has_local_commits
                implementing.metadata.implementation.iteration = implementing.metadata.cycle
                implementing.metadata.implementation.last_result = "success" if success else "failure"
                if not has_changes:
                    implementing.metadata.errors.append(TaskErrorInfo(code="implementation-no-changes", message="implementer produced no workspace changes"))
                if has_changes and not has_substantive_changes:
                    implementing.metadata.errors.append(
                        TaskErrorInfo(
                            code="implementation-non-code-changes",
                            message="implementer changed only docs, notes, reports, or other non-implementation artifacts",
                        )
                    )
                if has_local_commits:
                    implementing.metadata.errors.append(TaskErrorInfo(code="implementation-local-commits", message="implementer must not create local git commits"))
                if not result.ok:
                    implementing.metadata.errors.append(TaskErrorInfo(code="implementation-failed", message=result.stderr.strip() or "implementer run failed"))
                if success and not result.assistant_text.strip():
                    success = False
                    implementing.metadata.implementation.last_result = "failure"
                    implementing.metadata.errors.append(TaskErrorInfo(code="implementation-artifact-failed", message="implementer did not produce a final work artifact"))
                if success:
                    work_name = f"WORK-{implementing.metadata.cycle:03d}"
                    self.write_result_artifacts(implementing.task_dir, work_name, result)
                    clear_retry_gate(implementing.metadata)
                    self.metadata_store.save(implementing.task_dir, implementing.metadata)
                    done = self.transitions.move(implementing, TaskState.WAITING_REVIEWS, by=self.worker_name)
                else:
                    if any(error.code == "implementation-artifact-failed" for error in implementing.metadata.errors):
                        apply_retry_gate(implementing.metadata, reason="implementation-artifact-failed")
                        note = "implementation artifact generation failed"
                    elif not has_changes:
                        apply_retry_gate(implementing.metadata, reason="implementation-no-changes")
                        note = "implementation produced no workspace changes"
                    elif not has_substantive_changes:
                        apply_retry_gate(implementing.metadata, reason="implementation-non-code-changes")
                        note = "implementation changed only non-implementation artifacts"
                    elif has_local_commits:
                        apply_retry_gate(implementing.metadata, reason="implementation-local-commits")
                        note = "implementation created local commits"
                    else:
                        apply_retry_gate(implementing.metadata, reason="implementation-failed")
                        note = "implementation failed"
                    self.metadata_store.save(implementing.task_dir, implementing.metadata)
                    done = self.transitions.move(implementing, TaskState.TODOS, by=self.worker_name, note=note)
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True

            self.append_log_marker(log_path=log_path, phase="handshake", cycle=cycle)
            handshake_result = await asyncio.to_thread(
                adapter.run,
                agent=run_config.role_agent("implementer"),
                prompt=handshake_prompt,
                cwd=workspace_repo,
                run_log_path=log_path,
                config=run_config,
                include_directories=self._opencode_include_directories(run_config, implementing.metadata, workspace_repo),
                session_id=session_id,
                cancel_key=implementing.metadata.task_id,
            )
            implementing.metadata.implementation.resolved_model = handshake_result.resolved_model
            implementing.metadata.implementation.session_id = handshake_result.session_id
            implementing.metadata.implementation.last_run_tokens = handshake_result.total_tokens
            implementing.metadata.implementation.session_tokens = self.next_session_token_total(
                reused_session_id=session_id,
                returned_session_id=handshake_result.session_id,
                prior_session_tokens=prior_session_tokens,
                run_tokens=self.session_budget_tokens(handshake_result),
            )
            self.metadata_store.save(implementing.task_dir, implementing.metadata)
            drift_note = self._target_repo_state_drift_note(implementing.metadata)
            if drift_note is not None:
                implementing.metadata.implementation.last_result = "failure"
                implementing.metadata.errors.append(
                    TaskErrorInfo(code="implementation-target-repo-drift", message=drift_note)
                )
                apply_retry_gate(implementing.metadata, reason="implementation-target-repo-drift")
                self.metadata_store.save(implementing.task_dir, implementing.metadata)
                done = self.transitions.move(implementing, TaskState.TODOS, by=self.worker_name, note=drift_note)
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True
            if not handshake_result.ok:
                implementing.metadata.implementation.last_result = "failure"
                implementing.metadata.errors.append(
                    TaskErrorInfo(code="implementation-failed", message=handshake_result.stderr.strip() or "implementer handshake failed")
                )
                apply_retry_gate(implementing.metadata, reason="implementation-failed")
                self.metadata_store.save(implementing.task_dir, implementing.metadata)
                done = self.transitions.move(implementing, TaskState.TODOS, by=self.worker_name, note="implementation handshake failed")
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True

            active_session_id = handshake_result.session_id or session_id
            self.append_log_marker(log_path=log_path, phase="live", cycle=cycle)
            live_result = await self._run_adapter_with_retry(
                adapter=adapter,
                implementing=implementing,
                prompt=live_prompt,
                workspace_repo=workspace_repo,
                run_log_path=log_path,
                log_name=log_name,
                run_config=run_config,
                session_id=active_session_id,
                loop=loop,
                output_format="default",
                stream_stderr_to_log=True,
            )

            implementing.metadata.implementation.resolved_model = live_result.resolved_model or handshake_result.resolved_model
            implementing.metadata.implementation.session_id = live_result.session_id or active_session_id
            implementing.metadata.implementation.last_run_tokens = live_result.total_tokens
            implementing.metadata.implementation.session_tokens = self.next_session_token_total(
                reused_session_id=active_session_id,
                returned_session_id=live_result.session_id,
                prior_session_tokens=implementing.metadata.implementation.session_tokens,
                run_tokens=self.session_budget_tokens(live_result),
            )
            implementing.metadata.cycle += 1
            drift_note = self._target_repo_state_drift_note(implementing.metadata)
            if drift_note is not None:
                implementing.metadata.implementation.last_result = "failure"
                implementing.metadata.errors.append(
                    TaskErrorInfo(code="implementation-target-repo-drift", message=drift_note)
                )
                apply_retry_gate(implementing.metadata, reason="implementation-target-repo-drift")
                self.metadata_store.save(implementing.task_dir, implementing.metadata)
                done = self.transitions.move(implementing, TaskState.TODOS, by=self.worker_name, note=drift_note)
                await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                return True
            changes = self.workspace_changes(workspace_repo)
            has_changes = bool(changes)
            has_substantive_changes = self._has_allowed_implementation_changes(implementing.task_dir, changes)
            has_local_commits = self.workspace_has_local_commits(workspace_repo, implementing.metadata.target.base_branch)
            success = live_result.ok and has_changes and has_substantive_changes and not has_local_commits
            implementing.metadata.implementation.iteration = implementing.metadata.cycle
            implementing.metadata.implementation.last_result = "success" if success else "failure"

            if not has_changes:
                implementing.metadata.errors.append(
                    TaskErrorInfo(code="implementation-no-changes", message="implementer produced no workspace changes")
                )
            if has_local_commits:
                implementing.metadata.errors.append(
                    TaskErrorInfo(code="implementation-local-commits", message="implementer must not create local git commits")
                )
            if has_changes and not has_substantive_changes:
                implementing.metadata.errors.append(
                    TaskErrorInfo(
                        code="implementation-non-code-changes",
                        message="implementer changed only docs, notes, reports, or other non-implementation artifacts",
                    )
                )
            if not live_result.ok:
                implementing.metadata.errors.append(
                    TaskErrorInfo(code="implementation-failed", message=live_result.stderr.strip() or "implementer run failed")
                )

            if success:
                self.append_log_marker(log_path=log_path, phase="finalize", cycle=cycle)
                finalize_result = await asyncio.to_thread(
                    adapter.run,
                    agent=run_config.role_agent("implementer"),
                    prompt=finalize_prompt,
                    cwd=workspace_repo,
                    run_log_path=log_path,
                    config=run_config,
                    include_directories=self._opencode_include_directories(run_config, implementing.metadata, workspace_repo),
                    session_id=live_result.session_id or active_session_id,
                    cancel_key=implementing.metadata.task_id,
                )
                implementing.metadata.implementation.resolved_model = (
                    finalize_result.resolved_model or live_result.resolved_model or handshake_result.resolved_model
                )
                implementing.metadata.implementation.session_id = finalize_result.session_id or live_result.session_id or active_session_id
                implementing.metadata.implementation.last_run_tokens = finalize_result.total_tokens
                implementing.metadata.implementation.session_tokens = self.next_session_token_total(
                    reused_session_id=active_session_id,
                    returned_session_id=finalize_result.session_id,
                    prior_session_tokens=implementing.metadata.implementation.session_tokens,
                    run_tokens=self.session_budget_tokens(finalize_result),
                )
                drift_note = self._target_repo_state_drift_note(implementing.metadata)
                if drift_note is not None:
                    implementing.metadata.implementation.last_result = "failure"
                    implementing.metadata.errors.append(
                        TaskErrorInfo(code="implementation-target-repo-drift", message=drift_note)
                    )
                    apply_retry_gate(implementing.metadata, reason="implementation-target-repo-drift")
                    self.metadata_store.save(implementing.task_dir, implementing.metadata)
                    done = self.transitions.move(implementing, TaskState.TODOS, by=self.worker_name, note=drift_note)
                    await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                    return True
                if not finalize_result.ok or not finalize_result.assistant_text.strip():
                    implementing.metadata.implementation.last_result = "failure"
                    implementing.metadata.errors.append(
                        TaskErrorInfo(
                            code="implementation-artifact-failed",
                            message=finalize_result.stderr.strip() or "implementer did not produce a final work artifact",
                        )
                    )
                    apply_retry_gate(implementing.metadata, reason="implementation-artifact-failed")
                    self.metadata_store.save(implementing.task_dir, implementing.metadata)
                    done = self.transitions.move(
                        implementing,
                        TaskState.TODOS,
                        by=self.worker_name,
                        note="implementation artifact generation failed",
                    )
                    await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
                    return True
                finalized_result = RunResult(
                    ok=finalize_result.ok,
                    returncode=finalize_result.returncode,
                    assistant_text=finalize_result.assistant_text,
                    stdout=finalize_result.stdout,
                    stderr=finalize_result.stderr,
                    raw_events_path=finalize_result.raw_events_path,
                    command=finalize_result.command,
                    resolved_model=implementing.metadata.implementation.resolved_model,
                    session_id=implementing.metadata.implementation.session_id,
                    total_tokens=finalize_result.total_tokens,
                    session_budget_tokens=finalize_result.session_budget_tokens,
                )
                work_name = f"WORK-{implementing.metadata.cycle:03d}"
                self.write_result_artifacts(implementing.task_dir, work_name, finalized_result)
                clear_retry_gate(implementing.metadata)
                self.metadata_store.save(implementing.task_dir, implementing.metadata)
                done = self.transitions.move(implementing, TaskState.WAITING_REVIEWS, by=self.worker_name)
            else:
                if not has_changes:
                    apply_retry_gate(implementing.metadata, reason="implementation-no-changes")
                    note = "implementation produced no workspace changes"
                elif not has_substantive_changes:
                    apply_retry_gate(implementing.metadata, reason="implementation-non-code-changes")
                    note = "implementation changed only non-implementation artifacts"
                elif has_local_commits:
                    apply_retry_gate(implementing.metadata, reason="implementation-local-commits")
                    note = "implementation created local commits"
                else:
                    apply_retry_gate(implementing.metadata, reason="implementation-failed")
                    note = "implementation failed"
                self.metadata_store.save(implementing.task_dir, implementing.metadata)
                done = self.transitions.move(implementing, TaskState.TODOS, by=self.worker_name, note=note)
        await self.emit("task_moved", done.metadata.task_id, state=done.state.value)
        return True

    async def _prepare_workspace(self, task):
        try:
            return await asyncio.to_thread(self.workspace_manager.prepare, task.metadata)
        except WorkspaceSyncError as exc:
            existing_workspace = Path(task.metadata.implementation.workspace or "")
            if not existing_workspace.exists():
                raise
            await asyncio.to_thread(self.workspace_manager.discard, task.metadata)
            self._reset_implementation_context(task.metadata)
            task.metadata.errors.append(
                TaskErrorInfo(code="implementation-base-sync-conflict", message=str(exc))
            )
            self.metadata_store.save(task.task_dir, task.metadata)
            return await asyncio.to_thread(self.workspace_manager.prepare, task.metadata)

    def _reset_implementation_context(self, metadata) -> None:
        metadata.implementation.target_repo_baseline = None
        metadata.implementation.last_result = None
        metadata.implementation.resolved_model = None
        metadata.implementation.session_id = None
        metadata.implementation.last_run_tokens = 0
        metadata.implementation.session_tokens = 0

    def _capture_target_repo_baseline(self, metadata) -> None:
        snapshot = snapshot_target_repo_state(Path(metadata.target.repo_root), base_branch=metadata.target.base_branch)
        metadata.implementation.target_repo_baseline = snapshot.model_copy(deep=True)

    def _target_repo_state_drift_note(self, metadata) -> str | None:
        baseline = metadata.implementation.target_repo_baseline
        if baseline is None:
            return None
        current = snapshot_target_repo_state(Path(metadata.target.repo_root), base_branch=metadata.target.base_branch)
        if baseline.current_branch != current.current_branch:
            return (
                "target repo current branch changed from "
                f"{baseline.current_branch or '(detached)'} to {current.current_branch or '(detached)'}"
            )
        head_drift = describe_target_repo_head_drift(
            expected_branch=baseline.current_branch,
            expected_head_sha=baseline.head_sha,
            current_branch=current.current_branch,
            current_head_sha=current.head_sha,
        )
        if head_drift is not None:
            return head_drift
        if baseline.dirty or not current.dirty:
            return None
        summary = current.status_short.splitlines()[0].strip() if current.status_short.strip() else "working tree is dirty"
        return f"target repo working tree became dirty on {current.current_branch or '(detached)'}: {summary}"

    def _resolve_implementer_run_config(self, task_dir: Path, metadata):
        run_config = self.resolve_task_run_config(task_dir, metadata)
        backend_override = metadata.implementation.resume_backend_override
        model_override = metadata.implementation.resume_model_override
        if backend_override is None and model_override is None:
            return run_config
        overridden = run_config.model_copy(deep=True)
        if backend_override is not None:
            overridden.set_role_backend("implementer", backend_override)
        overridden.set_role_model("implementer", model_override)
        metadata.implementation.resume_mode = None
        metadata.implementation.resume_backend_override = None
        metadata.implementation.resume_model_override = None
        self.metadata_store.save(task_dir, metadata)
        return overridden

    def _resolve_implementer_adapter(self, run_config) -> AssistantAdapter:
        backend = run_config.backend_for_role("implementer")
        adapter = self.adapter_registry.get(backend)
        if adapter is None:
            adapter = self.adapter
        availability_error = adapter.availability_error(config=run_config, backend=backend)
        if availability_error is not None:
            raise AdapterRunError(f"{backend} backend is unavailable for implementer: {availability_error}")
        return adapter

    def _has_allowed_implementation_changes(self, task_dir: Path, changes) -> bool:
        substantive_changes = [change for change in changes if not self._is_non_implementation_artifact(change.path)]
        if not substantive_changes:
            return self._task_allows_docs_only_changes(task_dir)
        if all(change.is_new_file for change in substantive_changes):
            return self._task_allows_new_files(task_dir)
        return True

    def _is_non_implementation_artifact(self, path: Path) -> bool:
        normalized = path.as_posix().strip("/")
        docs_root = self.config.target_repo_docs_root.strip().strip("/")
        if docs_root and (normalized == docs_root or normalized.startswith(f"{docs_root}/")):
            return True
        return path.suffix.lower() in {".adoc", ".markdown", ".md", ".rst"}

    def _task_allows_docs_only_changes(self, task_dir: Path) -> bool:
        task_text = self._task_intent_text(task_dir)
        docs_terms = (
            "update documentation",
            "write documentation",
            "create documentation",
            "readme",
            "문서 작성",
            "문서 수정",
            "문서 업데이트",
            "문서를 작성",
            "문서를 수정",
            "마크다운 작성",
            "마크다운 문서",
        )
        return self._contains_positive_intent(task_text, docs_terms)

    def _task_allows_new_files(self, task_dir: Path) -> bool:
        task_text = self._task_intent_text(task_dir)
        new_file_terms = (
            "create a new file",
            "create new files",
            "add a new file",
            "add new files",
            "add tests",
            "add test",
            "새 파일을 생성",
            "신규 파일을 생성",
            "새 파일을 추가",
            "신규 파일을 추가",
            "테스트를 추가",
        )
        return self._contains_positive_intent(task_text, new_file_terms)

    def _contains_positive_intent(self, task_text: str, terms: tuple[str, ...]) -> bool:
        negation_markers = (
            "do not",
            "don't",
            "does not",
            "without",
            "must not",
            "not ",
            "no ",
            "금지",
            "하지 않는다",
            "하지 않",
            "하지 말",
            "하지말",
            "생성하지",
            "추가하지",
            "수정하지",
            "없이",
            "아니",
        )
        for term in terms:
            lowered_term = term.lower()
            start = 0
            while True:
                index = task_text.find(lowered_term, start)
                if index == -1:
                    break
                window_start = max(0, index - 48)
                window_end = min(len(task_text), index + len(lowered_term) + 48)
                context = task_text[window_start:window_end]
                if not any(marker in context for marker in negation_markers):
                    return True
                start = index + len(lowered_term)
        return False

    def _task_intent_text(self, task_dir: Path) -> str:
        parts: list[str] = []
        for name in ("REQUEST.md", "PLAN.md"):
            path = task_dir / name
            if path.exists():
                parts.append(path.read_text(errors="replace"))
        return "\n".join(parts).lower()

    def _opencode_include_directories(self, run_config, metadata, workspace_repo: Path) -> list[Path] | None:
        if run_config.backend_for_role("implementer") != "opencode":
            return None
        target_repo_root = Path(metadata.target.repo_root).expanduser().resolve()
        if target_repo_root == workspace_repo:
            return None
        return [target_repo_root]

    async def _run_adapter_with_retry(
        self,
        *,
        adapter,
        implementing,
        prompt: str,
        workspace_repo: Path,
        run_log_path: Path,
        log_name: str,
        run_config,
        session_id: str | None,
        loop,
        output_format: str = "json",
        stream_stderr_to_log: bool = False,
        show_thinking: bool = False,
    ) -> RunResult:
        result = await asyncio.to_thread(
            adapter.run,
            agent=run_config.role_agent("implementer"),
            prompt=prompt,
            cwd=workspace_repo,
            run_log_path=run_log_path,
            config=run_config,
            include_directories=self._opencode_include_directories(run_config, implementing.metadata, workspace_repo),
            session_id=session_id,
            cancel_key=implementing.metadata.task_id,
            on_log_line=self.make_log_callback(loop, implementing.metadata.task_id, log_name),
            output_format=output_format,
            stream_stderr_to_log=stream_stderr_to_log,
            show_thinking=show_thinking,
        )
        if not self._is_interrupted_run(result):
            return result
        return await asyncio.to_thread(
            adapter.run,
            agent=run_config.role_agent("implementer"),
            prompt=prompt,
            cwd=workspace_repo,
            run_log_path=run_log_path,
            config=run_config,
            include_directories=self._opencode_include_directories(run_config, implementing.metadata, workspace_repo),
            session_id=session_id,
            cancel_key=implementing.metadata.task_id,
            on_log_line=self.make_log_callback(loop, implementing.metadata.task_id, log_name),
            output_format=output_format,
            stream_stderr_to_log=stream_stderr_to_log,
            show_thinking=show_thinking,
        )

    def _is_interrupted_run(self, result) -> bool:
        return (
            not result.ok
            and result.returncode < 0
            and not result.stdout.strip()
            and not result.stderr.strip()
            and not result.assistant_text.strip()
        )

    def _build_implementer_source(self, task_dir):
        sections = ["# Plan", "", (task_dir / "PLAN.md").read_text().rstrip()]
        latest_review = sorted(task_dir.glob("REVIEW-*.md"))
        if latest_review:
            sections.extend(["", "# Latest AI Review", "", latest_review[-1].read_text().rstrip()])
        latest_reviewer_qa = sorted(task_dir.glob("REVIEWER-QA-*.md"))
        if latest_reviewer_qa:
            sections.extend(["", "# Latest Reviewer Q&A", "", latest_reviewer_qa[-1].read_text().rstrip()])
        latest_human_verify = sorted(task_dir.glob("HUMAN-VERIFY-*.md"))
        if latest_human_verify:
            sections.extend(["", "# Latest Human Verification", "", latest_human_verify[-1].read_text().rstrip()])
        return "\n".join(section for section in sections if section is not None)

    def _build_handshake_prompt(self, metadata) -> str:
        requested_language = generation_language_name(metadata.request.language)
        return "\n".join(
            [
                "You are preparing a reusable fs-kanban implementer session.",
                f"Reply with one short greeting in {requested_language}.",
                "Do not analyze the plan yet.",
                "Do not modify files yet.",
                (
                    "The real target repo at "
                    f"`{metadata.target.repo_root}` is off-limits. "
                    "You will work only inside the current workspace once implementation begins."
                ),
                "Do not produce the final work artifact yet.",
            ]
        )

    def _build_finalize_prompt(self, task_dir, metadata) -> str:
        source = self._build_implementer_source(task_dir)
        instructions = "\n".join(
            [
                source,
                "",
                "# Finalize Work Artifact",
                "- Summarize the implementation that already exists in the current workspace.",
                "- Do not make additional file edits or create git commits.",
                "- Return only the final markdown artifact with the required sections.",
            ]
        )
        return self.build_prompt(instructions, metadata, phase="implementer")
