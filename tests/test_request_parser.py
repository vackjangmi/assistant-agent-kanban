from __future__ import annotations

from assistant_agent_kanban.request_parser import extract_goal_text, has_required_request_fields


def test_request_parser_extracts_goal_from_korean_heading():
    content = "\n".join(
        [
            "---",
            "title: 한국어 요청",
            "language: ko",
            "target:",
            "  repo_root: /tmp/repo",
            "  base_branch: main",
            "---",
            "",
            "# 한국어 요청",
            "",
            "## 목표",
            "새 버튼 위치를 조정한다.",
            "",
            "## 범위",
            "- 필요한 파일만 수정한다.",
            "",
        ]
    )

    assert extract_goal_text(content) == "새 버튼 위치를 조정한다."
    assert has_required_request_fields(content) is True
