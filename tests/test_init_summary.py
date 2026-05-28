from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_summary_script(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", "-c", script],
        cwd=REPO_ROOT,
        env={**os.environ, "TERM": "dumb"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_init_summary_renders_final_values_and_next_command():
    result = run_summary_script(
        ". ./lib/init_summary.sh\n"
        "VENV_DIR=/tmp/assistant-agent-kanban/.venv\n"
        "CONFIG_PATH=/tmp/assistant-agent-kanban/config.yaml\n"
        "CONFIG_WRITE_PATH=/tmp/assistant-agent-kanban/config.local.yaml\n"
        "REPO_DISCOVERY_ROOT=/Users/sooyeol24/git\n"
        "CODING_ASSISTANT=claude\n"
        "LANGUAGE=KO\n"
        "THEME=light\n"
        "init_print_summary\n"
    )

    assert result.returncode == 0, result.stderr
    assert "Assistant Agent Kanban is ready" in result.stdout
    assert "Files" in result.stdout
    assert "Virtualenv            /tmp/assistant-agent-kanban/.venv" in result.stdout
    assert "Base config           /tmp/assistant-agent-kanban/config.yaml" in result.stdout
    assert "Local config          /tmp/assistant-agent-kanban/config.local.yaml" in result.stdout
    assert "Setup selections" in result.stdout
    assert "Repo discovery root   /Users/sooyeol24/git" in result.stdout
    assert "Assistant             claude" in result.stdout
    assert "UI language           KO" in result.stdout
    assert "UI theme              light" in result.stdout
    assert "Start the dashboard" in result.stdout
    assert "  ./run.sh" in result.stdout
    assert "next:" not in result.stdout
    assert "Initialized assistant-agent-kanban" not in result.stdout
