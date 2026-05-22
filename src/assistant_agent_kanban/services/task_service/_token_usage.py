from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

if TYPE_CHECKING:
    from ._protocol import _TaskServiceLike
else:
    _TaskServiceLike = object

from ...config import SUPPORTED_RUNTIME_ASSISTANTS
from ...models import (
    TaskContext,
    TaskMetadata,
)

from ._data import (
    ASSISTANT_RESULT_ARTIFACT_RE,
    AssistantTokenUsageRow,
    RUNTIME_ASSISTANT_LABELS,
    RUNTIME_ASSISTANT_METADATA_FIELDS,
    TokenUsageBreakdown,
)


class _TokenUsageMixin(_TaskServiceLike):
    def _assistant_token_usage_rows(self, task: TaskContext) -> list[AssistantTokenUsageRow]:
        grouped: dict[tuple[str, str, str], dict[str, object]] = {}
        for artifact_path in sorted(task.task_dir.glob("*.json")):
            if artifact_path.is_symlink():
                continue
            role = self._runtime_role_for_result_artifact(artifact_path.name)
            if role is None:
                continue
            try:
                payload = json.loads(artifact_path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            self._add_assistant_usage_record(
                grouped,
                metadata=task.metadata,
                role=role,
                model=self._assistant_usage_model(payload, task.metadata, role),
                session_id=self._string_value(payload.get("session_id")),
                usage=self._token_breakdown_from_result_payload(payload),
            )

        self._add_reviewer_qa_usage(grouped, task)
        self._add_branch_summary_usage(grouped, task)

        rows = []
        for (runtime_assistant, used_assistant, model), aggregate in grouped.items():
            session_count = len(cast(set[str], aggregate["session_ids"])) + cast(int, aggregate["anonymous_sessions"])
            rows.append(
                AssistantTokenUsageRow(
                    runtime_assistant=runtime_assistant,
                    used_assistant=used_assistant,
                    model=model,
                    sessions=session_count,
                    input_tokens=cast(int, aggregate["input_tokens"]),
                    cached_tokens=cast(int, aggregate["cached_tokens"]),
                    output_tokens=cast(int, aggregate["output_tokens"]),
                    total_tokens=cast(int, aggregate["total_tokens"]),
                    input_unavailable_runs=cast(int, aggregate["input_unavailable_runs"]),
                    cached_unavailable_runs=cast(int, aggregate["cached_unavailable_runs"]),
                    output_unavailable_runs=cast(int, aggregate["output_unavailable_runs"]),
                    unavailable_runs=cast(int, aggregate["unavailable_runs"]),
                )
            )
        return sorted(rows, key=lambda row: (row.runtime_assistant, row.used_assistant, row.model))


    def _add_assistant_usage_record(
        self,
        grouped: dict[tuple[str, str, str], dict[str, object]],
        *,
        metadata: TaskMetadata,
        role: str,
        model: str,
        session_id: str | None,
        usage: TokenUsageBreakdown,
    ) -> None:
        runtime_assistant = RUNTIME_ASSISTANT_LABELS[role]
        used_assistant = self._used_assistant_label(metadata, role)
        key = (runtime_assistant, used_assistant, model)
        if key not in grouped:
            grouped[key] = self._empty_assistant_usage_aggregate()
        if session_id:
            cast(set[str], grouped[key]["session_ids"]).add(session_id)
        else:
            grouped[key]["anonymous_sessions"] = cast(int, grouped[key]["anonymous_sessions"]) + 1
        self._add_token_usage_to_aggregate(grouped[key], usage)


    def _runtime_role_for_result_artifact(self, filename: str) -> Literal["planner", "plan_approval", "implementer", "reviewer"] | None:
        if not ASSISTANT_RESULT_ARTIFACT_RE.match(filename):
            return None
        if filename == "PLAN.json" or filename.startswith("PLAN-REJECTED-"):
            return "planner"
        if filename == "PLAN-APPROVAL.json":
            return "plan_approval"
        if filename.startswith("WORK-"):
            return "implementer"
        if filename.startswith("REVIEW-"):
            return "reviewer"
        return None


    def _add_reviewer_qa_usage(self, grouped: dict[tuple[str, str, str], dict[str, object]], task: TaskContext) -> None:
        log_usage = self._usage_from_jsonl_log(self._task_runs_dir(task.metadata.task_id) / "reviewer-qa.jsonl")
        if log_usage is not None:
            self._add_assistant_usage_aggregate(
                grouped,
                metadata=task.metadata,
                role="reviewer_qa",
                model=self._reviewer_qa_model(task.metadata),
                session_ids=log_usage[0],
                usage=log_usage[1],
            )
            return
        if task.metadata.review.qa_session_id is None and task.metadata.review.qa_last_run_tokens <= 0:
            return
        self._add_assistant_usage_record(
            grouped,
            metadata=task.metadata,
            role="reviewer_qa",
            model=self._reviewer_qa_model(task.metadata),
            session_id=task.metadata.review.qa_session_id,
            usage=self._total_only_token_breakdown(task.metadata.review.qa_last_run_tokens if task.metadata.review.qa_last_run_tokens > 0 else None),
        )


    def _add_branch_summary_usage(self, grouped: dict[tuple[str, str, str], dict[str, object]], task: TaskContext) -> None:
        for log_path in sorted(self._task_runs_dir(task.metadata.task_id).glob("branch-summary-*.jsonl")):
            log_usage = self._usage_from_jsonl_log(log_path)
            if log_usage is None:
                continue
            self._add_assistant_usage_aggregate(
                grouped,
                metadata=task.metadata,
                role="branch_summary",
                model=self._assistant_usage_model({}, task.metadata, "branch_summary"),
                session_ids=log_usage[0],
                usage=log_usage[1],
            )


    def _add_assistant_usage_aggregate(
        self,
        grouped: dict[tuple[str, str, str], dict[str, object]],
        *,
        metadata: TaskMetadata,
        role: str,
        model: str,
        session_ids: set[str],
        usage: TokenUsageBreakdown,
    ) -> None:
        runtime_assistant = RUNTIME_ASSISTANT_LABELS[role]
        used_assistant = self._used_assistant_label(metadata, role)
        key = (runtime_assistant, used_assistant, model)
        if key not in grouped:
            grouped[key] = self._empty_assistant_usage_aggregate()
        if session_ids:
            cast(set[str], grouped[key]["session_ids"]).update(session_ids)
        else:
            grouped[key]["anonymous_sessions"] = cast(int, grouped[key]["anonymous_sessions"]) + 1
        self._add_token_usage_to_aggregate(grouped[key], usage)


    def _usage_from_jsonl_log(self, log_path: Path) -> tuple[set[str], TokenUsageBreakdown] | None:
        if not log_path.exists() or log_path.is_symlink():
            return None
        usage = TokenUsageBreakdown()
        session_ids: set[str] = set()
        try:
            lines = log_path.read_text().splitlines()
        except OSError:
            return None
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("====="):
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            session_id = self._session_id_from_payload(payload)
            if session_id:
                session_ids.add(session_id)
            usage = usage.merge(self._token_breakdown_from_payload(payload))
        if not usage.has_total and not session_ids:
            return None
        return session_ids, usage


    def _empty_assistant_usage_aggregate(self) -> dict[str, object]:
        return {
            "session_ids": set(),
            "anonymous_sessions": 0,
            "input_tokens": 0,
            "cached_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "input_unavailable_runs": 0,
            "cached_unavailable_runs": 0,
            "output_unavailable_runs": 0,
            "unavailable_runs": 0,
        }


    def _add_token_usage_to_aggregate(self, aggregate: dict[str, object], usage: TokenUsageBreakdown) -> None:
        if usage.has_input:
            aggregate["input_tokens"] = cast(int, aggregate["input_tokens"]) + usage.input_tokens
        if usage.input_unavailable_units > 0:
            aggregate["input_unavailable_runs"] = cast(int, aggregate["input_unavailable_runs"]) + usage.input_unavailable_units
        elif not usage.has_input:
            aggregate["input_unavailable_runs"] = cast(int, aggregate["input_unavailable_runs"]) + 1
        if usage.has_cached:
            aggregate["cached_tokens"] = cast(int, aggregate["cached_tokens"]) + usage.cached_tokens
        if usage.cached_unavailable_units > 0:
            aggregate["cached_unavailable_runs"] = cast(int, aggregate["cached_unavailable_runs"]) + usage.cached_unavailable_units
        elif not usage.has_cached:
            aggregate["cached_unavailable_runs"] = cast(int, aggregate["cached_unavailable_runs"]) + 1
        if usage.has_output:
            aggregate["output_tokens"] = cast(int, aggregate["output_tokens"]) + usage.output_tokens
        if usage.output_unavailable_units > 0:
            aggregate["output_unavailable_runs"] = cast(int, aggregate["output_unavailable_runs"]) + usage.output_unavailable_units
        elif not usage.has_output:
            aggregate["output_unavailable_runs"] = cast(int, aggregate["output_unavailable_runs"]) + 1
        if usage.has_total:
            aggregate["total_tokens"] = cast(int, aggregate["total_tokens"]) + usage.total_tokens
        if usage.total_unavailable_units > 0:
            aggregate["unavailable_runs"] = cast(int, aggregate["unavailable_runs"]) + usage.total_unavailable_units
        elif not usage.has_total:
            aggregate["unavailable_runs"] = cast(int, aggregate["unavailable_runs"]) + 1


    def _session_id_from_payload(self, payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        for key in ("session_id", "sessionId", "sessionID", "thread_id"):
            value = self._string_value(payload.get(key))
            if value:
                return value
        for key in ("result", "event", "message", "part"):
            value = self._session_id_from_payload(payload.get(key))
            if value:
                return value
        return None


    def _token_breakdown_from_result_payload(self, payload: dict[str, object]) -> TokenUsageBreakdown:
        usage = TokenUsageBreakdown()
        stdout = payload.get("stdout")
        if isinstance(stdout, str):
            for raw_line in stdout.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    usage = usage.merge(self._token_breakdown_from_payload(json.loads(line)))
                except json.JSONDecodeError:
                    continue
        explicit_usage = self._token_breakdown_from_payload(payload)
        usage = usage.merge(explicit_usage)
        artifact_total = self._int_value(payload.get("total_tokens"))
        if artifact_total is not None and not usage.has_total:
            usage = usage.merge(self._total_only_token_breakdown(artifact_total))
        return usage


    def _total_only_token_breakdown(self, total_tokens: int | None) -> TokenUsageBreakdown:
        if total_tokens is None:
            return TokenUsageBreakdown()
        return TokenUsageBreakdown(
            total_tokens=total_tokens,
            has_total=True,
            input_unavailable_units=1,
            cached_unavailable_units=1,
            output_unavailable_units=1,
        )


    def _token_breakdown_from_payload(self, payload: object) -> TokenUsageBreakdown:
        if not isinstance(payload, dict):
            return TokenUsageBreakdown()
        tokens = payload.get("tokens")
        if isinstance(tokens, dict):
            return self._token_breakdown_from_tokens_object(tokens)
        usage = payload.get("usage")
        if isinstance(usage, dict):
            return self._token_breakdown_from_usage_object(usage)
        merged = TokenUsageBreakdown()
        for key in ("result", "event", "message", "part"):
            merged = merged.merge(self._token_breakdown_from_payload(payload.get(key)))
        return merged


    def _token_breakdown_from_tokens_object(self, tokens: dict[object, object]) -> TokenUsageBreakdown:
        input_tokens = self._first_int_value(tokens, ("input", "input_tokens", "read", "read_tokens"))
        output_tokens = self._first_int_value(tokens, ("output", "output_tokens", "write", "write_tokens"))
        cached_tokens = self._first_int_value(tokens, ("cached", "cached_tokens", "cached_input_tokens"))
        cache = tokens.get("cache")
        if cached_tokens is None and isinstance(cache, dict):
            cached_tokens = self._sum_int_values(cache, ("read", "write", "input", "output", "creation", "created"))
        total_tokens = self._first_int_value(tokens, ("total", "total_tokens", "totalTokens"))
        return self._make_token_breakdown(
            input_tokens=input_tokens,
            cached_tokens=cached_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )


    def _token_breakdown_from_usage_object(self, usage: dict[object, object]) -> TokenUsageBreakdown:
        input_tokens = self._first_int_value(usage, ("input_tokens", "inputTokens", "input", "prompt_tokens", "promptTokens"))
        output_tokens = self._first_int_value(usage, ("output_tokens", "outputTokens", "output", "completion_tokens", "completionTokens"))
        cached_input_tokens = self._sum_int_values(
            usage,
            (
                "cached_input_tokens",
                "cachedInputTokens",
                "cached_tokens",
            ),
        )
        additive_cached_tokens = self._sum_int_values(
            usage,
            (
                "cache_creation_input_tokens",
                "cacheCreationInputTokens",
                "cache_read_input_tokens",
                "cacheReadInputTokens",
            ),
        )
        cached_tokens = self._sum_optional_ints(cached_input_tokens, additive_cached_tokens)
        total_tokens = self._first_int_value(usage, ("total_tokens", "totalTokens", "total"))
        if total_tokens is None:
            total_tokens = self._sum_optional_ints(input_tokens, additive_cached_tokens, output_tokens)
        return self._make_token_breakdown(
            input_tokens=input_tokens,
            cached_tokens=cached_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )


    def _make_token_breakdown(
        self,
        *,
        input_tokens: int | None,
        cached_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
    ) -> TokenUsageBreakdown:
        computed_total = total_tokens
        if computed_total is None:
            subtotal = sum(value for value in (input_tokens, cached_tokens, output_tokens) if value is not None)
            if subtotal > 0:
                computed_total = subtotal
        return TokenUsageBreakdown(
            input_tokens=input_tokens or 0,
            cached_tokens=cached_tokens or 0,
            output_tokens=output_tokens or 0,
            total_tokens=computed_total or 0,
            has_input=input_tokens is not None,
            has_cached=cached_tokens is not None,
            has_output=output_tokens is not None,
            has_total=computed_total is not None,
            input_unavailable_units=1 if computed_total is not None and input_tokens is None else 0,
            cached_unavailable_units=1 if computed_total is not None and cached_tokens is None else 0,
            output_unavailable_units=1 if computed_total is not None and output_tokens is None else 0,
        )


    def _first_int_value(self, payload: dict[object, object], keys: tuple[str, ...]) -> int | None:
        for key in keys:
            value = self._int_value(payload.get(key))
            if value is not None:
                return value
        return None


    def _sum_int_values(self, payload: dict[object, object], keys: tuple[str, ...]) -> int | None:
        total = 0
        found = False
        for key in keys:
            value = self._int_value(payload.get(key))
            if value is not None:
                total += value
                found = True
        return total if found else None


    def _sum_optional_ints(self, *values: int | None) -> int | None:
        total = 0
        found = False
        for value in values:
            if value is not None:
                total += value
                found = True
        return total if found else None


    def _used_assistant_label(self, metadata: TaskMetadata, role: str) -> str:
        backend = None
        runtime_pin = metadata.runtime_pin
        if runtime_pin is not None:
            role_backends = runtime_pin.role_backends
            backend_role = "reviewer" if role == "reviewer_qa" else "planner" if role == "branch_summary" else role
            backend = getattr(role_backends, backend_role, None)
            if backend is None:
                backend = runtime_pin.backend
        if isinstance(backend, str):
            return SUPPORTED_RUNTIME_ASSISTANTS.get(backend, backend)
        return "unknown"


    def _assistant_usage_model(self, payload: dict[str, object], metadata: TaskMetadata, role: str) -> str:
        payload_model = self._string_value(payload.get("resolved_model"))
        if payload_model:
            return payload_model
        model_role = "reviewer" if role == "reviewer_qa" else "planner" if role == "branch_summary" else role
        runtime_pin = metadata.runtime_pin
        if runtime_pin is not None:
            pinned_model = self._string_value(getattr(runtime_pin, f"{model_role}_model", None))
            if pinned_model:
                return pinned_model
        role_info = getattr(metadata, RUNTIME_ASSISTANT_METADATA_FIELDS.get(role, role), None)
        if role_info is not None:
            metadata_model = self._string_value(getattr(role_info, "resolved_model", None))
            if metadata_model:
                return metadata_model
        return "unknown"


    def _reviewer_qa_model(self, metadata: TaskMetadata) -> str:
        return metadata.review.qa_resolved_model or metadata.review.resolved_model or self._assistant_usage_model({}, metadata, "reviewer")


    def _format_token_total(self, total_tokens: int, unavailable_runs: int) -> str:
        if total_tokens == 0 and unavailable_runs > 0:
            return "unavailable"
        formatted = f"{total_tokens:,}"
        if unavailable_runs > 0:
            return f"{formatted} ({unavailable_runs} unavailable)"
        return formatted


    def _markdown_table_cell(self, value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("`", "&#96;")
            .replace("!", "&#33;")
            .replace("[", "&#91;")
            .replace("]", "&#93;")
            .replace("(", "&#40;")
            .replace(")", "&#41;")
            .replace("\n", " ")
            .replace("|", "\\|")
        )


    def _string_value(self, value: object) -> str | None:
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None


    def _int_value(self, value: object) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            normalized = value.replace(",", "").strip()
            if normalized.isdigit():
                return int(normalized)
        return None
