from __future__ import annotations

import io
import json
from pathlib import Path

from openpyxl import Workbook

from app.config import Settings
from app.db import Database
from app.excel_io import build_results_workbook, parse_submissions
from app.media import MediaEvidence
from app.repository import RepositoryEvidence, discover_media, normalize_github_url
from app.scoring import _normalize_result, evaluate_submission
from app.schemas import CRITERIA, CriterionScore, FirmGenEvidence, ModelEvaluation


def test_normalize_github_url():
    assert normalize_github_url("https://github.com/octocat/Hello-World/") == "https://github.com/octocat/Hello-World"


def test_discover_readme_media():
    readme = """
    ![Task list](docs/task.png)
    https://github.com/user-attachments/assets/12345678-demo
    [Demo](https://youtu.be/example)
    """
    urls = discover_media(readme, "owner", "repo", "main", "README.md")
    assert "https://raw.githubusercontent.com/owner/repo/main/docs/task.png" in urls
    assert "https://github.com/user-attachments/assets/12345678-demo" in urls
    assert "https://youtu.be/example" in urls


def test_excel_parser_and_export():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Team Name", "GitHub Repository URL"])
    sheet.append(["Alpha", "https://github.com/octocat/Hello-World"])
    sheet.append(["Alpha", "https://github.com/octocat/Hello-World"])
    stream = io.BytesIO()
    workbook.save(stream)

    rows = parse_submissions("responses.xlsx", stream.getvalue())
    assert rows == [{"team_id": "Alpha", "repo_url": "https://github.com/octocat/Hello-World"}]

    output = build_results_workbook(
        [
            {
                "team_id": "Alpha",
                "repo_url": rows[0]["repo_url"],
                "project_title": "Hello-World",
                "status": "completed",
                "commit_sha": "abc123",
                "total_score": 12.5,
                "overall_confidence": 0.7,
                "manual_review_recommended": False,
                "updated_at": "2026-08-05T00:00:00Z",
                "result": {"criteria": [], "project_summary": "A small project.", "review_reasons": []},
            }
        ]
    )
    assert output[:2] == b"PK"


def test_database_deduplicates(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    first, created = database.create_or_get_job("Team A", "https://github.com/octocat/Hello-World")
    second, created_again = database.create_or_get_job("Team A", "https://github.com/octocat/Hello-World/")
    assert created is True
    assert created_again is False
    assert first["id"] == second["id"]


def _firmware_repo(readme: str = "FirmGen task list used for the project.") -> RepositoryEvidence:
    return RepositoryEvidence(
        repo_url="https://github.com/octocat/firmware-project",
        owner="octocat",
        repo="firmware-project",
        branch="main",
        commit_sha="abc",
        readme_path="README.md",
        readme_text=readme,
        file_tree=["README.md", "platformio.ini", "src/main.cpp"],
        source_files={
            "platformio.ini": "[env:device]\nplatform = test\nframework = arduino",
            "src/main.cpp": "#include <Arduino.h>\nvoid setup() { Serial.begin(115200); }\nvoid loop() {}",
        },
        media_urls=[],
        warnings=["No README-linked screenshots or videos were found."],
    )


def test_non_firmware_repository_is_zero(tmp_path: Path):
    settings = Settings(mock_openai=True, data_dir=tmp_path)
    repo = RepositoryEvidence(
        repo_url="https://github.com/octocat/Hello-World",
        owner="octocat",
        repo="Hello-World",
        branch="master",
        commit_sha="abc",
        readme_path="README",
        readme_text="Hello World!",
        file_tree=["README"],
        source_files={"README": "Hello World!"},
        media_urls=[],
    )
    result = evaluate_submission(repo, MediaEvidence(), settings)
    assert result.total_score == 0
    assert result.firmware_eligible is False
    assert result.model_used == "firmware-eligibility-gate"


def test_mock_firmware_scoring_applies_firmgen_floor(tmp_path: Path):
    settings = Settings(mock_openai=True, data_dir=tmp_path)
    result = evaluate_submission(_firmware_repo(), MediaEvidence(), settings)
    firmgen = next(item for item in result.criteria if item.criterion_id == "firmgen_usage")
    assert result.firmware_eligible is True
    assert result.model_used == "mock"
    assert firmgen.score >= 15
    assert any("functionality capped" in item.lower() for item in result.caps_applied)
    assert json.loads(result.model_dump_json())["total_score"] == result.total_score


def test_strong_firmgen_evidence_gets_eighteen_point_floor():
    repo = _firmware_repo()
    model_result = ModelEvaluation(
        project_title="Firmware Project",
        project_summary="A test result.",
        observed_demo="",
        criteria=[
            CriterionScore(
                criterion_id=criterion_id,
                score=1,
                confidence=0.8,
                rationale="Model score before policy adjustment.",
                evidence=["README.md"],
                missing_evidence=[],
            )
            for criterion_id in CRITERIA
        ],
        firmgen_evidence=FirmGenEvidence(
            usage_detected=True,
            confidence=0.9,
            task_list_state="substantially_progressed",
            doxygen_or_desktop_visible=True,
            evidence_sources=["README task-list screenshot", "Doxygen report screenshot"],
            rationale="The task list is mostly marked complete and a related desktop/report view is visible.",
        ),
        manual_review_recommended=False,
        review_reasons=[],
    )
    result = _normalize_result(model_result, repo, MediaEvidence(), "test", False)
    firmgen = next(item for item in result.criteria if item.criterion_id == "firmgen_usage")
    assert firmgen.score == 18
    assert any("18/20" in item for item in result.caps_applied)
