from __future__ import annotations

import io
import zipfile

from app.config import Settings
from app.repository import collect_repository


def make_archive() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "sample-main/README.md",
            "# Tiny ESP32 idea\n\n![FirmGen task list](docs/tasks.png)\n",
        )
        archive.writestr("sample-main/platformio.ini", "[env:esp32]\nplatform = espressif32\n")
        archive.writestr("sample-main/src/main.cpp", "void setup() {}\\nvoid loop() {}\\n")
    return buffer.getvalue()


def test_collect_repository_from_archive(monkeypatch, tmp_path):
    monkeypatch.setattr("app.repository.resolve_head", lambda _: ("main", "deadbeef"))
    monkeypatch.setattr("app.repository._download_archive", lambda *args: make_archive())
    evidence = collect_repository(
        "https://github.com/example/sample",
        Settings(mock_openai=True, data_dir=tmp_path),
    )
    assert evidence.commit_sha == "deadbeef"
    assert evidence.source_file_count >= 1
    assert evidence.firmware_eligibility().eligible is True
    assert evidence.media_urls == [
        "https://raw.githubusercontent.com/example/sample/main/docs/tasks.png"
    ]
