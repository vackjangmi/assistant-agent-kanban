from __future__ import annotations

from assistant_agent_kanban.request_parser import extract_goal_text, has_required_request_fields, parse_request_markdown


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


def test_request_parser_reads_plan_auto_approve_from_front_matter():
    parsed = parse_request_markdown(
        "\n".join(
            [
                "---",
                "title: auto plan request",
                "language: en",
                "plan_auto_approve: true",
                "target:",
                "  repo_root: /tmp/repo",
                "  base_branch: main",
                "---",
                "",
                "# auto plan request",
                "",
                "## Goal",
                "Auto-approve the generated plan.",
            ]
        )
    )

    assert parsed.plan_auto_approve is True


def test_request_parser_recovers_known_fields_from_invalid_yaml_front_matter():
    parsed = parse_request_markdown(
        "\n".join(
            [
                "---",
                "title: Sonar cleanup: PAYMENT_ID constant",
                "plan_auto_approve: true",
                "target:",
                "  repo_root: /tmp/repo",
                "  base_branch: main",
                "---",
                "",
                "# Rename disposition fields",
                "",
                "## Goal",
                "Keep startup recovery alive even when front matter is malformed.",
            ]
        )
    )

    assert parsed.title == "Sonar cleanup: PAYMENT_ID constant"
    assert parsed.target_repo_root == "/tmp/repo"
    assert parsed.base_branch == "main"
    assert parsed.plan_auto_approve is True
    assert parsed.body.lstrip().startswith("# Rename disposition fields")
    assert "front matter is malformed" in parsed.body


def test_request_parser_accepts_required_korean_request_with_colon_title():
    content = "\n".join(
        [
            "---",
            "title: Sonar 지적사항 정리: PAYMENT_ID 상수화 및 Mapper 테스트 assertion 축소",
            "language: ko",
            "plan_auto_approve: true",
            "target:",
            "  repo_root: /tmp/repo",
            "  base_branch: cms-advances/track_0430",
            "---",
            "",
            "# Sonar 지적사항 정리: PAYMENT_ID 상수화 및 Mapper 테스트 assertion 축소",
            "",
            "## 목표",
            "Sonar 지적사항을 정리한다.",
            "",
        ]
    )

    assert has_required_request_fields(content) is True
