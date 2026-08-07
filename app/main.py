from __future__ import annotations

import hmac
import io
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeSerializer

from .config import get_settings
from .db import Database
from .excel_io import SpreadsheetError, build_results_workbook, parse_submissions
from .repository import RepositoryError, normalize_github_url
from .schemas import RepositorySubmission
from .worker import EvaluationWorker


BASE_DIR = Path(__file__).resolve().parent
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    database = Database(settings.database_path)
    worker = EvaluationWorker(database, settings)
    app.state.database = database
    app.state.worker = worker
    await worker.start()
    yield
    await worker.stop()


app = FastAPI(
    title="FirmGen Evaluator",
    description="Evidence-based firmware hackathon submission evaluator.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
serializer = URLSafeSerializer(settings.session_secret, salt="firmgen-evaluator-auth")


def _is_authenticated(request: Request) -> bool:
    if not settings.app_password:
        return True
    token = request.cookies.get("firmgen_auth", "")
    try:
        return serializer.loads(token) == "authenticated"
    except BadSignature:
        return False


@app.middleware("http")
async def authentication_and_headers(request: Request, call_next):
    public_paths = {"/login", "/health"}
    if request.url.path not in public_paths and not request.url.path.startswith("/static/"):
        if not _is_authenticated(request):
            if request.url.path.startswith("/api/"):
                return JSONResponse({"detail": "Authentication required."}, status_code=401)
            return RedirectResponse("/login", status_code=303)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "mode": "mock" if settings.mock_openai else "openai",
        "workers": settings.max_concurrent_evaluations,
    }


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if _is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": ""})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form(...)):
    if not settings.app_password or hmac.compare_digest(password, settings.app_password):
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            "firmgen_auth",
            serializer.dumps("authenticated"),
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            max_age=60 * 60 * 12,
        )
        return response
    return templates.TemplateResponse(
        request, "login.html", {"error": "That access password is incorrect."}, status_code=401
    )


@app.post("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie("firmgen_auth")
    return response


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "mock_mode": settings.mock_openai,
            "worker_count": settings.max_concurrent_evaluations,
            "model": settings.openai_model,
        },
    )


def _public_job(job: dict) -> dict:
    return {
        key: value
        for key, value in job.items()
        if key not in {"submission_key", "result_json"}
    }


@app.get("/api/jobs")
async def list_jobs(request: Request):
    jobs = request.app.state.database.list_jobs()
    return {"jobs": [_public_job(job) for job in jobs]}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    job = request.app.state.database.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Submission not found.")
    return _public_job(job)


@app.post("/api/submit")
async def submit_repository(submission: RepositorySubmission, request: Request):
    try:
        repo_url = normalize_github_url(submission.repo_url)
    except RepositoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job, created = request.app.state.database.create_or_get_job(
        submission.team_id, repo_url, force=submission.force
    )
    if created:
        await request.app.state.worker.enqueue(job["id"])
    return {"created": created, "job": _public_job(job)}


@app.post("/api/upload")
async def upload_submissions(request: Request, file: UploadFile = File(...)):
    content = await file.read(5 * 1024 * 1024 + 1)
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Spreadsheet exceeds 5 MB.")
    try:
        submissions = parse_submissions(file.filename or "submissions.xlsx", content)
    except SpreadsheetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    created_jobs = []
    duplicates = 0
    invalid: list[dict[str, str]] = []
    for submission in submissions:
        try:
            repo_url = normalize_github_url(submission["repo_url"])
        except RepositoryError as exc:
            invalid.append({"team_id": submission["team_id"], "error": str(exc)})
            continue
        job, created = request.app.state.database.create_or_get_job(
            submission["team_id"], repo_url
        )
        if created:
            created_jobs.append(job)
            await request.app.state.worker.enqueue(job["id"])
        else:
            duplicates += 1

    return {
        "created": len(created_jobs),
        "duplicates": duplicates,
        "invalid": invalid,
        "jobs": [_public_job(job) for job in created_jobs],
    }


@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str, request: Request):
    current = request.app.state.database.get_job(job_id)
    if not current:
        raise HTTPException(status_code=404, detail="Submission not found.")
    job, _ = request.app.state.database.create_or_get_job(
        current["team_id"], current["repo_url"], force=True
    )
    await request.app.state.worker.enqueue(job["id"])
    return {"job": _public_job(job)}


@app.get("/api/export.xlsx")
async def export_results(request: Request):
    workbook = build_results_workbook(request.app.state.database.list_jobs())
    return StreamingResponse(
        io.BytesIO(workbook),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="firmgen-evaluation-results.xlsx"'},
    )
