from __future__ import annotations

import base64
import ipaddress
import io
import mimetypes
import socket
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import requests
from PIL import Image, UnidentifiedImageError

from .config import Settings


@dataclass
class ImageEvidence:
    label: str
    data_url: str
    kind: str
    source_url: str


@dataclass
class MediaEvidence:
    images: list[ImageEvidence] = field(default_factory=list)
    transcript: str = ""
    discovered_urls: list[str] = field(default_factory=list)
    processed_videos: int = 0
    processed_images: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def has_video_evidence(self) -> bool:
        return self.processed_videos > 0

    @property
    def has_visual_evidence(self) -> bool:
        return bool(self.images)


class MediaError(RuntimeError):
    pass


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise MediaError("Only public HTTP(S) media URLs are supported.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise MediaError("Media hostname could not be resolved.") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise MediaError("Private or reserved media addresses are blocked.")


def _download(url: str, destination: Path, max_bytes: int) -> tuple[str, str]:
    _validate_public_url(url)
    response = requests.get(
        url,
        headers={"User-Agent": "FirmGen-Evaluator/1.0"},
        stream=True,
        timeout=(10, 60),
        allow_redirects=True,
    )
    response.raise_for_status()
    _validate_public_url(response.url)
    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
    total = 0
    with destination.open("wb") as handle:
        for chunk in response.iter_content(256 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise MediaError("Media file exceeds the configured size limit.")
            handle.write(chunk)
    return content_type, response.url


def _image_to_data_url(path: Path) -> str:
    try:
        with Image.open(path) as image:
            image.seek(0)
            image = image.convert("RGB")
            image.thumbnail((1600, 1600))
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=82, optimize=True)
    except (UnidentifiedImageError, OSError) as exc:
        raise MediaError("Downloaded file is not a readable image.") from exc
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _download_youtube(url: str, directory: Path, max_bytes: int) -> Path:
    try:
        import yt_dlp
    except ImportError as exc:
        raise MediaError("YouTube support is not installed.") from exc

    output_template = str(directory / "youtube.%(ext)s")
    options = {
        "format": "best[height<=720][ext=mp4]/best[height<=720]/best",
        "outtmpl": output_template,
        "quiet": True,
        "noplaylist": True,
        "max_filesize": max_bytes,
        "socket_timeout": 30,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(url, download=True)
        filename = Path(downloader.prepare_filename(info))
    if not filename.exists():
        candidates = list(directory.glob("youtube.*"))
        if not candidates:
            raise MediaError("YouTube video could not be downloaded.")
        filename = candidates[0]
    if filename.stat().st_size > max_bytes:
        raise MediaError("Video exceeds the configured size limit.")
    return filename


def _extract_frames(video_path: Path, directory: Path, limit: int) -> list[Path]:
    if limit <= 0:
        return []
    pattern = directory / "frame_%03d.jpg"
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_path),
        "-vf", "fps=1/5,scale='min(1280,iw)':-2", "-frames:v", str(limit), str(pattern),
    ]
    try:
        subprocess.run(command, capture_output=True, timeout=120, check=True)
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise MediaError("FFmpeg could not extract video frames.") from exc
    return sorted(directory.glob("frame_*.jpg"))[:limit]


def _extract_audio(video_path: Path, directory: Path) -> Path | None:
    output = directory / "audio.mp3"
    command = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-b:a", "64k", str(output),
    ]
    try:
        subprocess.run(command, capture_output=True, timeout=120, check=True)
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    if not output.exists() or output.stat().st_size == 0 or output.stat().st_size > 25 * 1024 * 1024:
        return None
    return output


def _looks_like_youtube(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"}


def _kind_from(content_type: str, url: str) -> str:
    if content_type.startswith("image/"):
        return "image"
    if content_type.startswith("video/"):
        return "video"
    suffix = Path(urlparse(url).path).suffix.lower()
    guessed = mimetypes.guess_type(url)[0] or ""
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"} or guessed.startswith("image/"):
        return "image"
    if suffix in {".mp4", ".mov", ".m4v", ".webm"} or guessed.startswith("video/"):
        return "video"
    return "unknown"


def process_media(
    urls: list[str],
    settings: Settings,
    transcription_client=None,
) -> MediaEvidence:
    result = MediaEvidence(discovered_urls=urls)
    image_budget = settings.max_readme_images
    video_budget = settings.max_videos
    max_bytes = settings.max_media_mb * 1024 * 1024

    with tempfile.TemporaryDirectory(prefix="firmgen-media-", dir=settings.temp_dir) as temp_name:
        root = Path(temp_name)
        for index, url in enumerate(urls):
            if image_budget <= 0 and video_budget <= 0:
                break
            try:
                item_dir = root / f"item-{index}"
                item_dir.mkdir()
                if _looks_like_youtube(url):
                    if video_budget <= 0:
                        continue
                    media_path = _download_youtube(url, item_dir, max_bytes)
                    kind = "video"
                    final_url = url
                else:
                    media_path = item_dir / "media"
                    content_type, final_url = _download(url, media_path, max_bytes)
                    kind = _kind_from(content_type, final_url)

                if kind == "image" and image_budget > 0:
                    result.images.append(
                        ImageEvidence(
                            label=f"README image {result.processed_images + 1}",
                            data_url=_image_to_data_url(media_path),
                            kind="readme_image",
                            source_url=url,
                        )
                    )
                    result.processed_images += 1
                    image_budget -= 1
                elif kind == "video" and video_budget > 0:
                    frames = _extract_frames(media_path, item_dir, settings.max_video_frames)
                    for frame_index, frame in enumerate(frames):
                        result.images.append(
                            ImageEvidence(
                                label=f"Video {result.processed_videos + 1}, frame near {frame_index * 5}s",
                                data_url=_image_to_data_url(frame),
                                kind="video_frame",
                                source_url=url,
                            )
                        )
                    audio = _extract_audio(media_path, item_dir)
                    if audio and transcription_client is not None:
                        try:
                            with audio.open("rb") as handle:
                                transcription = transcription_client.audio.transcriptions.create(
                                    model="gpt-transcribe", file=handle
                                )
                            text = getattr(transcription, "text", "")
                            if text:
                                prefix = f"[Video {result.processed_videos + 1} transcript] "
                                result.transcript += prefix + text.strip() + "\n"
                        except Exception as exc:
                            result.warnings.append(f"Video transcript failed: {type(exc).__name__}.")
                    result.processed_videos += 1
                    video_budget -= 1
            except (MediaError, requests.RequestException, OSError) as exc:
                result.warnings.append(f"Skipped media {url}: {str(exc)[:180]}")

    if urls and not result.images:
        result.warnings.append("README media links were found, but none could be processed.")
    return result
