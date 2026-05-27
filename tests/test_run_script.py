from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_run_script_defaults_to_wildcard_host(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    venv_dir = tmp_path / "venv"
    bin_dir = venv_dir / "bin"
    bin_dir.mkdir(parents=True)
    (venv_dir / ".assistant-agent-kanban-deps-stamp").write_text("", encoding="utf-8")
    fake_cli = bin_dir / "assistant-agent-kanban"
    args_file = tmp_path / "args.txt"
    fake_cli.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" > "$ASSISTANT_AGENT_KANBAN_TEST_ARGS"\n',
        encoding="utf-8",
    )
    fake_cli.chmod(0o755)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("kanban_root: ./kanban\n", encoding="utf-8")

    env = {
        **os.environ,
        "VENV_DIR": str(venv_dir),
        "CONFIG_PATH": str(config_path),
        "ASSISTANT_AGENT_KANBAN_TEST_ARGS": str(args_file),
    }
    result = subprocess.run(
        [str(repo_root / "run.sh"), "--port", "7777"],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert args_file.read_text(encoding="utf-8").splitlines() == [
        "serve",
        "--config",
        str(config_path),
        "--host",
        "0.0.0.0",
        "--port",
        "7777",
    ]
