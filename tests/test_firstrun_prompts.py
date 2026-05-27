from __future__ import annotations

import os
import subprocess
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


def test_firstrun_choice_falls_back_to_numbered_selection():
    result = run_prompt_script(
        '. ./lib/firstrun_prompts.sh\n'
        '_firstrun_prompt_choice "Choose UI language:" 1 EN KO\n'
        'printf "choice=%s\\n" "$_CHOICE"\n',
        input_text="2\n",
    )

    assert result.returncode == 0, result.stderr
    assert "choice=KO" in result.stdout


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
