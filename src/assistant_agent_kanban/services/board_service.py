from __future__ import annotations

from typing import Protocol

from ..models import BoardSnapshot


class BoardSnapshotProvider(Protocol):
    def board_snapshot(self) -> BoardSnapshot: ...


class BoardService:
    def __init__(self, scanner: BoardSnapshotProvider) -> None:
        self.scanner = scanner
        self._cached_board: BoardSnapshot | None = None

    def get_board(self) -> BoardSnapshot:
        if self._cached_board is None:
            self._cached_board = self.scanner.board_snapshot()
        return self._cached_board

    def refresh_board(self) -> BoardSnapshot:
        self._cached_board = self.scanner.board_snapshot()
        return self._cached_board
