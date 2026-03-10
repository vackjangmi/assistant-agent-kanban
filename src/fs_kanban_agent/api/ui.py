from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


def build_ui_router() -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return """
<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>FS Kanban Agent</title>
  <style>
    body { font-family: Georgia, serif; margin: 0; background: linear-gradient(180deg, #f7f2e8, #e8eef5); color: #182026; }
    header { padding: 16px 20px; display: flex; justify-content: space-between; align-items: center; }
    button { padding: 8px 12px; border: 1px solid #182026; background: #fff9ef; cursor: pointer; }
    #board { display: grid; grid-template-columns: repeat(5, minmax(220px, 1fr)); gap: 12px; padding: 0 20px 20px; }
    .column { background: rgba(255,255,255,0.72); border: 1px solid rgba(24,32,38,0.15); padding: 12px; min-height: 160px; }
    .card { background: white; border-left: 4px solid #7c4f2c; padding: 10px; margin: 10px 0; box-shadow: 0 4px 10px rgba(0,0,0,0.05); }
    @media (max-width: 900px) { #board { grid-template-columns: repeat(2, minmax(180px, 1fr)); } }
  </style>
</head>
<body>
  <header><h1>Filesystem Kanban Agent</h1><button id=\"refresh\">Refresh</button></header>
  <main id=\"board\"></main>
  <script>
    const board = document.getElementById('board');
    async function loadBoard() {
      const res = await fetch('/api/board');
      const data = await res.json();
      board.innerHTML = data.columns.map((column) => `
        <section class="column">
          <h2>${column.state}</h2>
          ${column.items.map((item) => `<article class="card"><strong>${item.title}</strong><div>${item.task_id}</div><div>iter ${item.iteration}</div></article>`).join('')}
        </section>`).join('');
    }
    document.getElementById('refresh').addEventListener('click', loadBoard);
    const source = new EventSource('/api/events');
    source.addEventListener('board_snapshot', loadBoard);
    loadBoard();
  </script>
</body>
</html>
"""

    return router
