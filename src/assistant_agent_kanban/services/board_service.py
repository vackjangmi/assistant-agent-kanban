from __future__ import annotations

from ..models import BoardSnapshot
from ..scanner import KanbanScanner


class BoardService:
    def __init__(self, scanner: KanbanScanner) -> None:
        self.scanner = scanner
        self._cached_board: BoardSnapshot | None = None

    def get_board(self) -> BoardSnapshot:
        if self._cached_board is None:
            self._cached_board = self.scanner.board_snapshot()
        return self._cached_board

    def refresh_board(self) -> BoardSnapshot:
        self._cached_board = self.scanner.board_snapshot()
        return self._cached_board
