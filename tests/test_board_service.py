from __future__ import annotations

from fs_kanban_agent.models import BoardSnapshot
from fs_kanban_agent.services.board_service import BoardService


class RecordingScanner:
    def __init__(self) -> None:
        self.calls = 0

    def board_snapshot(self) -> BoardSnapshot:
        self.calls += 1
        return BoardSnapshot(columns=[])


def test_board_service_caches_board_snapshot() -> None:
    scanner = RecordingScanner()
    service = BoardService(scanner)

    first = service.get_board()
    second = service.get_board()

    assert first is second
    assert scanner.calls == 1


def test_board_service_refresh_rebuilds_cached_snapshot() -> None:
    scanner = RecordingScanner()
    service = BoardService(scanner)

    first = service.get_board()
    refreshed = service.refresh_board()

    assert refreshed is service.get_board()
    assert refreshed is not first
    assert scanner.calls == 2
