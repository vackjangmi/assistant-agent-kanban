from __future__ import annotations

import asyncio

from fs_kanban_agent.events import EventBus
from fs_kanban_agent.models import BoardSnapshot
from fs_kanban_agent.runtime import RuntimeSupervisor
from fs_kanban_agent.scanner import KanbanScanner
from fs_kanban_agent.metadata_store import MetadataStore

from .conftest import create_request_task


class DummyBoardService:
    def get_board(self) -> BoardSnapshot:
        return BoardSnapshot(columns=[])


class DummyRecoveryService:
    def recover(self):
        return []


class DummyModelRegistry:
    def __init__(self) -> None:
        self.adapter = object()


class BlockingWorker:
    def __init__(self, candidates):
        self._candidates = list(candidates)
        self.completed: set[str] = set()
        self.started: list[str] = []
        self.gate = asyncio.Event()

    def candidate_tasks(self):
        return [task for task in self._candidates if task.metadata.task_id not in self.completed]

    async def run_task(self, task) -> bool:
        self.started.append(task.metadata.task_id)
        await self.gate.wait()
        self.completed.add(task.metadata.task_id)
        return True


def test_runtime_supervisor_schedules_per_role_parallelism_without_duplicate_tasks(configured_paths):
    config, _, _ = configured_paths
    config.runtime.planner_agent_count = 2
    config.runtime.implementer_agent_count = 1
    config.runtime.reviewer_agent_count = 1

    create_request_task(config, "planner-one")
    create_request_task(config, "planner-two")
    create_request_task(config, "planner-three")
    create_request_task(config, "implementer-one")
    create_request_task(config, "reviewer-one")

    scanner = KanbanScanner(config, MetadataStore())
    tasks_by_name = {
        task.metadata.title: task
        for task in sorted(scanner.scan(), key=lambda item: item.metadata.title)
    }
    planner_worker = BlockingWorker(
        [
            tasks_by_name["planner-one"],
            tasks_by_name["planner-two"],
            tasks_by_name["planner-three"],
        ]
    )
    implementer_worker = BlockingWorker([tasks_by_name["implementer-one"]])
    reviewer_worker = BlockingWorker([tasks_by_name["reviewer-one"]])
    supervisor = RuntimeSupervisor(
        config,
        planner_worker,
        implementer_worker,
        reviewer_worker,
        object(),
        scanner,
        DummyBoardService(),
        object(),
        object(),
        object(),
        DummyRecoveryService(),
        EventBus(),
        DummyModelRegistry(),
    )

    async def scenario() -> None:
        scheduled = await supervisor.dispatch_once()
        assert scheduled is True
        await asyncio.sleep(0)
        assert planner_worker.started == [
            tasks_by_name["planner-one"].metadata.task_id,
            tasks_by_name["planner-two"].metadata.task_id,
        ]
        assert implementer_worker.started == [tasks_by_name["implementer-one"].metadata.task_id]
        assert reviewer_worker.started == [tasks_by_name["reviewer-one"].metadata.task_id]

        scheduled_again = await supervisor.dispatch_once()
        assert scheduled_again is False
        await asyncio.sleep(0)
        assert len(planner_worker.started) == 2
        assert len(implementer_worker.started) == 1
        assert len(reviewer_worker.started) == 1

        planner_tasks = list(supervisor._role_tasks["planner"])
        implementer_tasks = list(supervisor._role_tasks["implementer"])
        reviewer_tasks = list(supervisor._role_tasks["reviewer"])
        planner_worker.gate.set()
        implementer_worker.gate.set()
        reviewer_worker.gate.set()
        await asyncio.gather(*planner_tasks, *implementer_tasks, *reviewer_tasks)
        await asyncio.sleep(0)

        rescheduled = await supervisor.dispatch_once()
        assert rescheduled is True
        await asyncio.sleep(0)
        assert planner_worker.started == [
            tasks_by_name["planner-one"].metadata.task_id,
            tasks_by_name["planner-two"].metadata.task_id,
            tasks_by_name["planner-three"].metadata.task_id,
        ]
        assert implementer_worker.started == [tasks_by_name["implementer-one"].metadata.task_id]
        assert reviewer_worker.started == [tasks_by_name["reviewer-one"].metadata.task_id]

        trailing_tasks = list(supervisor._role_tasks["planner"])
        await asyncio.gather(*trailing_tasks)

    asyncio.run(scenario())
