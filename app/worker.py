from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress

from openai import OpenAI

from .config import Settings
from .db import Database
from .media import process_media
from .repository import collect_repository
from .scoring import evaluate_submission


logger = logging.getLogger(__name__)


class EvaluationWorker:
    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.tasks: list[asyncio.Task] = []
        self._enqueued: set[str] = set()

    async def start(self) -> None:
        for job_id in self.database.queued_job_ids():
            await self.enqueue(job_id)
        self.tasks = [
            asyncio.create_task(self._loop(index), name=f"evaluation-worker-{index}")
            for index in range(self.settings.max_concurrent_evaluations)
        ]

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            with suppress(asyncio.CancelledError):
                await task
        self.tasks.clear()

    async def enqueue(self, job_id: str) -> None:
        if job_id in self._enqueued:
            return
        self._enqueued.add(job_id)
        await self.queue.put(job_id)

    async def _loop(self, index: int) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                await asyncio.to_thread(self._run_job_sync, job_id)
            except Exception:
                logger.exception("Unhandled evaluation error for job %s", job_id)
            finally:
                self._enqueued.discard(job_id)
                self.queue.task_done()

    def _run_job_sync(self, job_id: str) -> None:
        job = self.database.get_job(job_id)
        if not job:
            return
        self.database.update_job(
            job_id, status="processing", progress="Collecting repository evidence", error=""
        )

        try:
            repo = collect_repository(job["repo_url"], self.settings)
            self.database.update_job(
                job_id,
                progress="Reading README media",
                commit_sha=repo.commit_sha,
                project_title=repo.repo,
            )

            client = None
            if self.settings.openai_api_key and not self.settings.mock_openai:
                client = OpenAI(
                    api_key=self.settings.openai_api_key,
                    timeout=180,
                    max_retries=2,
                )
            media = process_media(repo.media_urls, self.settings, transcription_client=client)
            self.database.update_job(job_id, progress="Scoring against the rubric")

            evaluation = evaluate_submission(repo, media, self.settings, client=client)
            payload = json.dumps(evaluation.model_dump(mode="json"), ensure_ascii=False)
            self.database.update_job(
                job_id,
                status="completed",
                progress="Evaluation complete",
                project_title=evaluation.project_title,
                commit_sha=repo.commit_sha,
                total_score=evaluation.total_score,
                overall_confidence=evaluation.overall_confidence,
                manual_review_recommended=int(evaluation.manual_review_recommended),
                result_json=payload,
                error="",
            )
        except Exception as exc:
            message = str(exc).strip() or type(exc).__name__
            logger.exception("Evaluation failed for %s", job["repo_url"])
            self.database.update_job(
                job_id,
                status="failed",
                progress="Evaluation failed",
                error=message[:500],
                manual_review_recommended=1,
            )
