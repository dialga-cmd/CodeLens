import asyncio
import json
import os
import queue
from typing import Any, AsyncIterator, Callable

import anyio
from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.core.ai_analyzer import AIAnalyzer
from app.core.analyzer import CodeAnalyzer
from app.core.auth import initialize_firebase, verify_token
from app.core.chat_context import guess_language, read_repo_file, resolve_repo_file
from app.core.chat_service import ChatService
from app.core.fix_advisor import FixAdvisor
from app.core.ingestion import RepoURLError, normalize_repo_url
from app.core.limits import AnalysisRegistry, RateLimits, TooManyAnalyses
from app.core.llm import LLMError, get_llm_client


app = FastAPI(
    title="CodeLens API",
    version="1.0.0",
    description=(
        "Repository analysis for CodeLens. Parses a GitHub repository, computes deterministic "
        "metrics, enriches them with NVIDIA Nemotron models served by Nebius Token Factory, and "
        "grounds dependency and security claims with live web search."
    ),
)
initialize_firebase()

allowed_origins = [
    origin.strip()
    for origin in os.getenv("CODELENS_ALLOWED_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]

allow_origin_regex = None
if os.getenv("ALLOW_VERCEL_ORIGINS", "true").lower() in {"1", "true", "yes", "on"}:
    allow_origin_regex = r"https://.*\.vercel\.app"

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=allow_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.on_event("startup")
async def expire_stale_snapshots() -> None:
    """Drop snapshots and clones that have outlived their time-to-live."""
    removed = analyzer.ingestion.expire()
    if removed:
        print(f"Removed {len(removed)} expired analyses: {', '.join(removed)}", flush=True)
    rate_limits.prune()

# Core services
analyzer = CodeAnalyzer()
rate_limits = RateLimits()
analysis_registry = AnalysisRegistry()


def rate_limit(kind: str) -> Callable[..., None]:
    """A dependency that spends one token from the bucket for this class of request.

    Analysis, chat and patch generation are separately limited because they cost
    very different amounts, and a single shared limit would either let one
    expensive request starve a cheap one or be useless for the expensive one.
    """

    def dependency(request: Request) -> None:
        allowed, retry_after = rate_limits.check(kind, rate_limits.client_key(request))
        if allowed:
            return
        raise HTTPException(
            status_code=429,
            detail=(
                f"Too many {kind} requests from this client. "
                f"Try again in {int(retry_after) + 1} seconds."
            ),
            headers={"Retry-After": str(int(retry_after) + 1)},
        )

    return dependency


@app.get("/health")
async def health_check():
    llm = get_llm_client()
    return {
        "status": "ok",
        "service": "codelens-api",
        "version": app.version,
        "llm": {
            "configured": llm.configured,
            "models": {"heavy": llm.heavy_model, "fast": llm.fast_model},
        },
        "research": {"configured": analyzer.research.available},
        "storage": analyzer.ingestion.stats(),
        "jobs": analysis_registry.describe(),
        "rate_limits": rate_limits.describe(),
    }


@app.get("/api/models")
async def list_models(user: dict = Depends(verify_token)):
    """The heavy/fast models this instance uses, plus the models the account can use."""
    llm = get_llm_client()
    payload: dict[str, Any] = {"configured": llm.configured, **llm.describe()}
    if llm.configured:
        try:
            payload["available"] = await anyio.to_thread.run_sync(llm.list_models)
        except LLMError as error:
            payload["available"] = []
            payload["error"] = str(error)
    return payload


@app.get("/analyze/stream")
async def analyze_repo_stream(
    url: str = Query(..., description="Public GitHub repository URL"),
    refresh: bool = Query(False, description="Re-run the analysis instead of reusing the stored one"),
    user: dict = Depends(verify_token),
    _rate: None = Depends(rate_limit("analyze")),
):
    """Analyse a repository, streaming progress as Server-Sent Events.

    Two requests for the same repository share one run: the second attaches to the
    first one's progress instead of cloning and paying for the same analysis
    twice. ``refresh=true`` re-runs the pipeline even if nothing changed.
    """
    try:
        canonical = normalize_repo_url(url)
    except RepoURLError as error:
        return EventSourceResponse(_error_events(str(error)))

    def runner(publish: Callable[[str], None]) -> dict[str, Any]:
        return analyzer.analyze_repo(canonical, progress_callback=publish, refresh=refresh)

    try:
        job, started = analysis_registry.start_or_attach(canonical, runner)
    except TooManyAnalyses as error:
        return EventSourceResponse(_error_events(str(error)))

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        subscription = job.subscribe()
        try:
            yield {
                "data": json.dumps(
                    {
                        "status": "starting",
                        "message": (
                            f"Starting analysis of {canonical}"
                            if started
                            else f"Attaching to the analysis of {canonical} that is already running"
                        ),
                    }
                )
            }

            while True:
                try:
                    message = subscription.get_nowait()
                except queue.Empty:
                    if job.finished.is_set():
                        break
                    await asyncio.sleep(0.25)
                    continue
                yield {"data": json.dumps({"status": "analyzing", "message": message})}

            if job.error:
                print(f"Analysis failed for {canonical}: {job.error}", flush=True)
                yield {"data": json.dumps({"status": "error", "message": job.error})}
                return

            results = job.result or {}
            yield {
                "data": json.dumps(
                    {
                        "status": "completed",
                        "message": f"Analysed {len(results.get('files', []))} files",
                        "results": results,
                    }
                )
            }
        finally:
            job.unsubscribe(subscription)

    return EventSourceResponse(event_generator())


class ChatRequest(BaseModel):
    repo_id: str
    query: str


class RepoFileRequest(BaseModel):
    repo_id: str
    file_path: str


@app.post("/chat")
async def chat(
    request: ChatRequest,
    user: dict = Depends(verify_token),
    _rate: None = Depends(rate_limit("chat")),
):
    snapshot = analyzer.ingestion.load_snapshot(request.repo_id)
    if not snapshot:
        return {
            "answer": "I could not find a stored snapshot for that repository yet. Run an analysis first, then ask again.",
            "sources": [],
        }

    service = ChatService(snapshot)
    if not service.client.configured:
        return {"error": "Model access is not configured on this server (NEBIUS_API_KEY is missing).", "answer": ""}

    return await service.answer(request.query)


@app.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    user: dict = Depends(verify_token),
    _rate: None = Depends(rate_limit("chat")),
):
    """Stream an answer as Server-Sent Events using the fast model."""
    snapshot = analyzer.ingestion.load_snapshot(request.repo_id)
    if not snapshot:
        return EventSourceResponse(
            _error_events("I could not find a stored snapshot for that repository yet. Run an analysis first.")
        )

    service = ChatService(snapshot)
    if not service.client.configured:
        return EventSourceResponse(_error_events("Model access is not configured on this server (NEBIUS_API_KEY is missing)."))

    return EventSourceResponse(service.stream(request.query))


class FixRequest(BaseModel):
    repo_id: str
    finding_id: str = ""
    file_path: str = ""
    line: int = 0


@app.post("/api/fix")
async def propose_fix(
    request: FixRequest,
    user: dict = Depends(verify_token),
    _rate: None = Depends(rate_limit("fix")),
):
    """Write a patch for one finding and report whether it applies to the clone."""
    snapshot = analyzer.ingestion.load_snapshot(request.repo_id)
    if not snapshot:
        return {"error": "Repository snapshot not found. Run an analysis first."}

    finding = _find_finding(snapshot, request)
    if finding is None:
        return {"error": "No finding matches that id or file and line in this analysis."}

    advisor = FixAdvisor(snapshot)
    if not advisor.available:
        return {
            "error": "Patch generation needs model access (NEBIUS_API_KEY) and the analysed clone.",
            "finding_id": finding.get("id", ""),
        }

    fix = await anyio.to_thread.run_sync(advisor.propose, finding)
    return {"finding": finding, "fix": fix.to_dict()}


def _find_finding(snapshot: dict[str, Any], request: FixRequest) -> dict[str, Any] | None:
    """Locate one finding by id, or by file and line as a fallback."""
    findings = [item for item in snapshot.get("vulnerabilities", []) if isinstance(item, dict)]
    if request.finding_id:
        for finding in findings:
            if str(finding.get("id", "")) == request.finding_id:
                return finding

    for finding in findings:
        if str(finding.get("file_path", "")) != request.file_path:
            continue
        if not request.line or int(finding.get("line", 0) or 0) == request.line:
            return finding
    return None


async def _error_events(message: str) -> AsyncIterator[dict[str, str]]:
    yield {"event": "error", "data": json.dumps({"message": message})}
    yield {"event": "done", "data": json.dumps({"ok": False})}


@app.post("/repo/file")
async def repo_file(
    request: RepoFileRequest,
    user: dict = Depends(verify_token),
    _rate: None = Depends(rate_limit("file")),
):
    snapshot = analyzer.ingestion.load_snapshot(request.repo_id)
    if not snapshot:
        return {"error": "Repository snapshot not found."}

    full_path, resolved_path = resolve_repo_file(snapshot.get("repo_path", ""), request.file_path)
    if not full_path:
        return {"error": "File not found in repository."}

    content = read_repo_file(snapshot.get("repo_path", ""), resolved_path, max_bytes=None)
    if content == "":
        return {"error": "File could not be read."}

    return {
        "repo_id": request.repo_id,
        "file_path": resolved_path,
        "content": content,
        "language": guess_language(resolved_path),
        "size": len(content),
    }


@app.get("/analyze/inspect")
async def inspect_analyzer():
    """Which model the analysis pipeline will use. Handy when checking a deployment."""
    model_analyzer = AIAnalyzer()
    return {"analysis_model": model_analyzer.model_id, "available": model_analyzer.available}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
