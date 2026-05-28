from __future__ import annotations

import errno
import os
import pty
import select
import subprocess
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_prompt_script(script: str, *, input_text: str = "", env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", "-c", script],
        cwd=REPO_ROOT,
        input=input_text,
        env={**os.environ, "TERM": "dumb", **(env or {})},
        capture_output=True,
        text=True,
        check=False,
    )


def run_prompt_script_pty(
    script: str, *, input_bytes: bytes = b"", env: dict[str, str] | None = None, input_delay: float = 0
) -> subprocess.CompletedProcess[str]:
    master_fd, slave_fd = pty.openpty()
    proc: subprocess.Popen[bytes] | None = None
    output = bytearray()

    def drain_output() -> None:
        while True:
            ready, _, _ = select.select([master_fd], [], [], 0)
            if not ready:
                return
            try:
                chunk = os.read(master_fd, 4096)
            except OSError as exc:
                if exc.errno == errno.EIO:
                    return
                raise
            if not chunk:
                return
            output.extend(chunk)

    try:
        proc = subprocess.Popen(
            ["sh", "-c", script],
            cwd=REPO_ROOT,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=subprocess.PIPE,
            env={**os.environ, "TERM": "xterm", **(env or {})},
        )
        os.close(slave_fd)
        slave_fd = -1
        if input_bytes:
            if input_delay:
                time.sleep(input_delay)
            os.write(master_fd, input_bytes)

        deadline = time.monotonic() + 5
        while proc.poll() is None:
            if time.monotonic() > deadline:
                proc.kill()
                raise TimeoutError("prompt script did not exit")
            ready, _, _ = select.select([master_fd], [], [], 0.1)
            if ready:
                drain_output()
        drain_output()
        stderr = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        return subprocess.CompletedProcess(
            args=["sh", "-c", script],
            returncode=proc.returncode,
            stdout=output.decode(errors="replace"),
            stderr=stderr,
        )
    finally:
        if slave_fd != -1:
            os.close(slave_fd)
        os.close(master_fd)
        if proc is not None and proc.poll() is None:
            proc.kill()


def test_firstrun_choice_falls_back_to_numbered_selection():
    result = run_prompt_script(
        '. ./lib/firstrun_prompts.sh\n'
        '_firstrun_prompt_choice "Choose UI language:" 1 EN KO\n'
        'printf "choice=%s\\n" "$_CHOICE"\n',
        input_text="2\n",
    )

    assert result.returncode == 0, result.stderr
    assert "choice=KO" in result.stdout


def test_firstrun_numbered_selection_allows_quit():
    result = run_prompt_script(
        "set -e\n"
        ". ./lib/firstrun_prompts.sh\n"
        '_firstrun_prompt_choice "Choose UI language:" 1 EN KO\n'
        'printf "choice=%s\\n" "$_CHOICE"\n',
        input_text="q\n",
    )

    assert result.returncode == 130
    assert "Setup cancelled." in result.stdout
    assert "choice=" not in result.stdout


def test_firstrun_key_selection_allows_quit():
    result = run_prompt_script_pty(
        "set -e\n"
        ". ./lib/firstrun_prompts.sh\n"
        '_firstrun_prompt_choice "Choose UI language:" 1 EN KO\n'
        'printf "choice=%s\\n" "$_CHOICE"\n',
        input_bytes=b"q",
    )

    assert result.returncode == 130
    assert "Setup cancelled." in result.stdout
    assert "choice=" not in result.stdout


def test_firstrun_assistant_prompt_uses_preferred_order(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for binary in ("claude", "codex", "agy", "gemini", "opencode"):
        executable = bin_dir / binary
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)

    result = run_prompt_script_pty(
        "set -e\n"
        ". ./lib/firstrun_prompts.sh\n"
        "FIRST_RUN_LOCAL_MISSING=1\n"
        "REPO_DISCOVERY_ROOT=/tmp\n"
        "CODING_ASSISTANT=\n"
        "LANGUAGE=EN\n"
        "THEME=dark\n"
        "firstrun_prompts\n",
        input_bytes=b"q",
        input_delay=0.2,
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert result.returncode == 130
    assert result.stdout.index("▶ claude  default") < result.stdout.index("   codex")
    assert result.stdout.index("   codex") < result.stdout.index("   antigravity")
    assert result.stdout.index("   antigravity") < result.stdout.index("   gemini")
    assert result.stdout.index("   gemini") < result.stdout.index("   opencode")


def test_firstrun_language_prompt_defaults_to_english():
    result = run_prompt_script_pty(
        "set -e\n"
        ". ./lib/firstrun_prompts.sh\n"
        "FIRST_RUN_LOCAL_MISSING=1\n"
        "REPO_DISCOVERY_ROOT=/tmp\n"
        "CODING_ASSISTANT=codex\n"
        "LANGUAGE=\n"
        "THEME=dark\n"
        "firstrun_prompts\n",
        input_bytes=b"q",
        input_delay=0.2,
    )

    assert result.returncode == 130
    assert "Setup step 1/2" in result.stdout
    assert "Choose UI language:" in result.stdout
    assert "▶ EN  default" in result.stdout


def test_firstrun_prompts_render_clean_intro_before_prompt(tmp_path):
    home = tmp_path / "home"
    repo_root = home / "git" / "assistant-agent-kanban"
    repo_root.mkdir(parents=True)

    result = run_prompt_script_pty(
        "set -e\n"
        ". ./lib/firstrun_prompts.sh\n"
        "FIRST_RUN_LOCAL_MISSING=1\n"
        f'HOME="{home}"\n'
        f'REPO_ROOT="{repo_root}"\n'
        "REPO_DISCOVERY_ROOT=\n"
        "CODING_ASSISTANT=codex\n"
        "LANGUAGE=EN\n"
        "THEME=dark\n"
        "firstrun_prompts\n",
        input_bytes=b"q",
    )

    assert result.returncode == 130
    assert "\x1b[2J\x1b[H" in result.stdout
    assert "\x1b[48;2;13;18;25m\x1b[38;2;216;207;252m" in result.stdout
    assert "\x1b[48;2;13;18;25m\x1b[38;2;0;169;232m" in result.stdout
    assert "\x1b[48;2;88;70;170m\x1b[38;2;245;243;255m" in result.stdout
    assert "Setup step 1/2" in result.stdout
    assert "█████╗ ███████╗███████╗" in result.stdout
    assert "██████╔╝███████║██╔██╗ ██║" in result.stdout
    assert "Setup cancelled." in result.stdout


def test_firstrun_prompts_redraw_between_steps_and_allow_back(tmp_path):
    home = tmp_path / "home"
    repo_root = home / "git" / "assistant-agent-kanban"
    repo_root.mkdir(parents=True)

    result = run_prompt_script_pty(
        "set -e\n"
        ". ./lib/firstrun_prompts.sh\n"
        "FIRST_RUN_LOCAL_MISSING=1\n"
        f'HOME="{home}"\n'
        f'REPO_ROOT="{repo_root}"\n'
        "REPO_DISCOVERY_ROOT=\n"
        "CODING_ASSISTANT=codex\n"
        "LANGUAGE=\n"
        "THEME=dark\n"
        "firstrun_prompts\n",
        input_bytes=b"\rbq",
        input_delay=0.2,
    )

    assert result.returncode == 130
    assert "Setup step 1/3" in result.stdout
    assert "Setup step 2/3" in result.stdout
    assert result.stdout.count("Repo discovery root") >= 2
    assert "b/Left to go back" in result.stdout
    assert "Setup cancelled." in result.stdout


def test_firstrun_prompts_review_values_before_continuing():
    result = run_prompt_script_pty(
        "set -e\n"
        ". ./lib/firstrun_prompts.sh\n"
        "FIRST_RUN_LOCAL_MISSING=1\n"
        "REPO_DISCOVERY_ROOT=/tmp\n"
        "CODING_ASSISTANT=codex\n"
        "LANGUAGE=EN\n"
        "THEME=\n"
        "firstrun_prompts\n"
        'printf "theme=%s\\n" "$THEME"\n',
        input_bytes=b"\r\r",
        input_delay=0.2,
    )

    assert result.returncode == 0, result.stderr
    assert "Setup step 1/2" in result.stdout
    assert "Setup step 2/2" in result.stdout
    assert "Review selections" in result.stdout
    assert "Repo discovery root: /tmp" in result.stdout
    assert "Coding assistant: codex" in result.stdout
    assert "UI language: EN" in result.stdout
    assert "UI theme: light" in result.stdout
    assert "theme=light" in result.stdout


def test_firstrun_prompts_review_screen_allows_back():
    result = run_prompt_script_pty(
        "set -e\n"
        ". ./lib/firstrun_prompts.sh\n"
        "FIRST_RUN_LOCAL_MISSING=1\n"
        "REPO_DISCOVERY_ROOT=/tmp\n"
        "CODING_ASSISTANT=codex\n"
        "LANGUAGE=EN\n"
        "THEME=\n"
        "firstrun_prompts\n",
        input_bytes=b"\rbq",
        input_delay=0.2,
    )

    assert result.returncode == 130
    assert "Review selections" in result.stdout
    assert result.stdout.count("UI theme") >= 2
    assert "Setup cancelled." in result.stdout


def test_firstrun_repo_root_defaults_to_home_git_when_repo_lives_under_it(tmp_path):
    home = tmp_path / "home"
    repo_root = home / "git" / "opencode_workboard"
    repo_root.mkdir(parents=True)

    result = run_prompt_script(
        '. ./lib/firstrun_prompts.sh\n'
        f'REPO_ROOT="{repo_root}"\n'
        "_firstrun_prompt_repo_root\n"
        'printf "root=%s\\n" "$REPO_DISCOVERY_ROOT"\n',
        input_text="\n",
        env={"HOME": str(home)},
    )

    assert result.returncode == 0, result.stderr
    assert f"root={home / 'git'}" in result.stdout


def test_firstrun_repo_root_allows_custom_path(tmp_path):
    home = tmp_path / "home"
    repo_root = tmp_path / "project" / "opencode_workboard"
    custom_root = tmp_path / "repos"
    home.mkdir()
    repo_root.mkdir(parents=True)

    result = run_prompt_script(
        '. ./lib/firstrun_prompts.sh\n'
        f'REPO_ROOT="{repo_root}"\n'
        "_firstrun_prompt_repo_root\n"
        'printf "root=%s\\n" "$REPO_DISCOVERY_ROOT"\n',
        input_text=f"4\n{custom_root}\n",
        env={"HOME": str(home)},
    )

    assert result.returncode == 0, result.stderr
    assert f"root={custom_root}" in result.stdout
