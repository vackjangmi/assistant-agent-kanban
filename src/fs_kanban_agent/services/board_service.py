from __future__ import annotations

from ..models import BoardSnapshot
from ..scanner import KanbanScanner


class BoardService:
    def __init__(self, scanner: KanbanScanner) -> None:
        self.scanner = scanner

    def get_board(self) -> BoardSnapshot:
        return self.scanner.board_snapshot()
