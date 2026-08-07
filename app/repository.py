from __future__ import annotations

import io
import re
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

import requests

from .config import Settings


GITHUB_RE = re.compile(
    r"^https?://(?:www\.)?github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)
URL_RE = re.compile(r"https?://[^\s<>'\"\])]+", re.IGNORECASE)
MARKDOWN_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+[\"'][^\"']*[\"'])?\)")
HTML_MEDIA_RE = re.compile(r"(?:src|href)\s*=\s*[\"']([^\"']+)[\"']", re.IGNORECASE)

TEXT_EXTENSIONS = {
    ".ino", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".py", ".js", ".jsx", ".ts", ".tsx",
    ".rs", ".go", ".java", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".html", ".css", ".sh", ".bat",
}
CODE_EXTENSIONS = {
    ".ino", ".c", ".cc", ".cpp", ".cxx", ".h", ".hpp", ".py", ".js", ".jsx",
    ".ts", ".tsx", ".rs", ".go", ".java",
}
IGNORE_PARTS = {
    ".git", "node_modules", "vendor", "dist", "build", ".next", ".venv", "venv",
    "__pycache__", "coverage", "target", ".pio", "libraries",
}
MEDIA_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".webm", ".m4v"}
FIRMWARE_CONFIG_NAMES = {
    "platformio.ini", "idf_component.yml", "sdkconfig", "partitions.csv", "arduino.json",
    "wokwi.toml", "firmware.toml", "mbed_app.json",
}
FIRMWARE_PATH_HINTS = ("firmware/", "embedded/", "device/", "src/", "sketch/")
FIRMWARE_MARKERS = (
    "#include <arduino", "#include <wifi", "#include <esp", "esp_", "freertos",
    "serial.begin", "[env:", "framework = arduino", "framework = espidf",
    "idf_component_register", "from machine import", "machine.pin", "machine.adc",
    "embedded-hal", "embassy_executor", "#![no_std]", "microcontroller.",
)


@dataclass(frozen=True)
class FirmwareEligibility:
    eligible: bool
    evidence: list[str]
    reason: str


@dataclass
class RepositoryEvidence:
    repo_url: str
    owner: str
    repo: str
    branch: str
    commit_sha: str
    readme_path: str
    readme_text: str
    file_tree: list[str]
    source_files: dict[str, str]
    media_urls: list[str]
    warnings: list[str] = field(default_factory=list)

    @property
    def source_file_count(self) -> int:
        return sum(1 for path in self.source_files if PurePosixPath(path).suffix.lower() in CODE_EXTENSIONS)

    def firmware_eligibility(self) -> FirmwareEligibility:
        """Decide the prerequisite from build/source evidence only, never README claims."""
        evidence: list[str] = []
        for path in self.file_tree:
            pure = PurePosixPath(path)
            name = pure.name.lower()
            suffix = pure.suffix.lower()
            if suffix == ".ino":
                evidence.append(f"Firmware sketch: {path}")
            elif name in FIRMWARE_CONFIG_NAMES:
                evidence.append(f"Embedded build/configuration file: {path}")

        marker_hits = 0
        for path, content in self.source_files.items():
            suffix = PurePosixPath(path).suffix.lower()
            if suffix not in CODE_EXTENSIONS and PurePosixPath(path).name.lower() not in FIRMWARE_CONFIG_NAMES:
                continue
            lower = content.lower()
            hits = [marker for marker in FIRMWARE_MARKERS if marker in lower]
            if hits:
                marker_hits += len(hits)
                evidence.append(f"Embedded firmware API/build markers in {path}: {', '.join(hits[:3])}")
            if suffix == ".ino" and "Firmware sketch" not in " ".join(evidence):
                evidence.append(f"Firmware sketch source: {path}")

        evidence = list(dict.fromkeys(evidence))[:8]
        eligible = bool(evidence) or marker_hits >= 2
        if eligible:
            return FirmwareEligibility(True, evidence, "Firmware build or embedded source evidence was found.")
        return FirmwareEligibility(
            False,
            [],
            "No firmware build configuration, firmware sketch, or embedded firmware source evidence was found.",
        )


class RepositoryError(RuntimeError):
    pass


def normalize_github_url(url: str) -> str:
    value = url.strip().split("#", 1)[0].split("?", 1)[0].rstrip("/")
    match = GITHUB_RE.match(value)
    if not match:
        raise RepositoryError("Only public GitHub repository URLs are supported.")
    owner, repo = match.group("owner"), match.group("repo")
    return f"https://github.com/{owner}/{repo}"


def parse_github_url(url: str) -> tuple[str, str]:
    normalized = normalize_github_url(url)
    match = GITHUB_RE.match(normalized)
    assert match
    return match.group("owner"), match.group("repo")


def resolve_head(repo_url: str) -> tuple[str, str]:
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--symref", repo_url, "HEAD"],
            capture_output=True,
            text=True,
            timeout=20,
            check=True,
        )
        branch = "main"
        commit = ""
        for line in result.stdout.splitlines():
            if line.startswith("ref:") and line.endswith("\tHEAD"):
                branch = line.split()[1].removeprefix("refs/heads/")
            elif line.endswith("\tHEAD") and not line.startswith("ref:"):
                commit = line.split()[0]
        return branch, commit
    except (FileNotFoundError, subprocess.SubprocessError):
        return "main", ""


def _download_archive(owner: str, repo: str, branch: str, settings: Settings) -> bytes:
    url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"
    headers = {"User-Agent": "FirmGen-Evaluator/1.0"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    limit = settings.max_repo_archive_mb * 1024 * 1024
    response = requests.get(url, headers=headers, stream=True, timeout=(10, 60))
    if response.status_code == 404 and branch == "main":
        return _download_archive(owner, repo, "master", settings)
    response.raise_for_status()
    data = bytearray()
    for chunk in response.iter_content(1024 * 256):
        data.extend(chunk)
        if len(data) > limit:
            raise RepositoryError(f"Repository archive exceeds {settings.max_repo_archive_mb} MB.")
    return bytes(data)


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members: list[zipfile.ZipInfo] = []
    total_size = 0
    for member in archive.infolist():
        path = PurePosixPath(member.filename)
        if member.is_dir() or path.is_absolute() or ".." in path.parts:
            continue
        total_size += member.file_size
        if total_size > 150 * 1024 * 1024:
            raise RepositoryError("Uncompressed repository content exceeds 150 MB.")
        members.append(member)
    return members


def _decode(data: bytes, limit: int) -> str:
    text = data[:limit].decode("utf-8", errors="replace").replace("\x00", "")
    text = re.sub(r"""(?i)(api[_-]?key|password|passwd|secret|token)(\s*[:=]\s*)["\'][^\s"\']+""", r"\1\2[REDACTED]", text)
    return re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[REDACTED_OPENAI_KEY]", text)


def _priority(path: str) -> tuple[int, int, str]:
    lower = path.lower()
    name = PurePosixPath(lower).name
    if name.startswith("readme"):
        tier = 0
    elif name in {"platformio.ini", "arduino.json", "diagram.json", "wokwi.toml"}:
        tier = 1
    elif name.startswith(("main.", "app.", "firmware.")):
        tier = 2
    elif "/test" in lower or name.startswith("test"):
        tier = 3
    elif PurePosixPath(lower).suffix in {".ino", ".cpp", ".c", ".h", ".hpp"}:
        tier = 4
    else:
        tier = 5
    return tier, len(path), path


def _resolve_media_url(raw_url: str, owner: str, repo: str, branch: str, readme_path: str) -> str | None:
    value = raw_url.strip().strip("<>")
    if value.startswith(("data:", "#", "mailto:")):
        return None
    if value.startswith("http://") or value.startswith("https://"):
        if "github.com" in value and "/blob/" in value:
            value = value.replace("https://github.com/", "https://raw.githubusercontent.com/", 1).replace("/blob/", "/", 1)
        return value
    readme_dir = str(PurePosixPath(readme_path).parent)
    if readme_dir == ".":
        readme_dir = ""
    base = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{readme_dir}/"
    return urljoin(base, value)


def discover_media(readme: str, owner: str, repo: str, branch: str, readme_path: str) -> list[str]:
    candidates: list[str] = []
    candidates.extend(MARKDOWN_LINK_RE.findall(readme))
    candidates.extend(HTML_MEDIA_RE.findall(readme))
    candidates.extend(URL_RE.findall(readme))
    seen: set[str] = set()
    results: list[str] = []
    for candidate in candidates:
        resolved = _resolve_media_url(candidate, owner, repo, branch, readme_path)
        if not resolved or resolved in seen:
            continue
        parsed = urlparse(resolved)
        suffix = PurePosixPath(parsed.path).suffix.lower()
        is_video_host = parsed.netloc.lower() in {
            "youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com",
        }
        if suffix in MEDIA_EXTENSIONS or "user-attachments/assets" in parsed.path or is_video_host:
            seen.add(resolved)
            results.append(resolved)
    return results[:40]


def collect_repository(repo_url: str, settings: Settings) -> RepositoryEvidence:
    normalized = normalize_github_url(repo_url)
    owner, repo = parse_github_url(normalized)
    branch, commit = resolve_head(normalized)
    try:
        archive_bytes = _download_archive(owner, repo, branch, settings)
    except requests.HTTPError as exc:
        raise RepositoryError(f"Unable to download public repository: HTTP {exc.response.status_code}.") from exc

    warnings: list[str] = []
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        members = _safe_members(archive)
        paths = [str(PurePosixPath(member.filename)) for member in members]
        root = paths[0].split("/", 1)[0] if paths else ""
        relative: list[tuple[str, zipfile.ZipInfo]] = []
        for path, member in zip(paths, members):
            rel = path[len(root) + 1 :] if root and path.startswith(root + "/") else path
            if rel:
                relative.append((rel, member))

        readmes = [(path, member) for path, member in relative if PurePosixPath(path).name.lower().startswith("readme")]
        if readmes:
            readme_path, readme_member = sorted(readmes, key=lambda item: (item[0].count("/"), len(item[0])))[0]
            readme_text = _decode(archive.read(readme_member), 60_000)
        else:
            readme_path, readme_text = "README.md", ""
            warnings.append("No README file was found.")

        candidates: list[tuple[str, zipfile.ZipInfo]] = []
        for path, member in relative:
            pure = PurePosixPath(path)
            if any(part.lower() in IGNORE_PARTS for part in pure.parts):
                continue
            if pure.suffix.lower() in TEXT_EXTENSIONS and member.file_size <= 400_000:
                candidates.append((path, member))

        source_files: dict[str, str] = {}
        used = 0
        for path, member in sorted(candidates, key=lambda item: _priority(item[0])):
            if len(source_files) >= 45 or used >= settings.max_repo_text_chars:
                break
            remaining = settings.max_repo_text_chars - used
            text = _decode(archive.read(member), min(12_000, remaining))
            if not text.strip():
                continue
            source_files[path] = text
            used += len(text)

        file_tree = [path for path, _ in relative[:800]]

    media_urls = discover_media(readme_text, owner, repo, branch, readme_path)
    if not media_urls:
        warnings.append("No README-linked screenshots or videos were found.")

    return RepositoryEvidence(
        repo_url=normalized,
        owner=owner,
        repo=repo,
        branch=branch,
        commit_sha=commit,
        readme_path=readme_path,
        readme_text=readme_text,
        file_tree=file_tree,
        source_files=source_files,
        media_urls=media_urls,
        warnings=warnings,
    )
