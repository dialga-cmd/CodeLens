import asyncio
import concurrent.futures
import json
import os
import queue
from typing import Any, AsyncIterator

import anyio
from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.core.ai_analyzer import AIAnalyzer
from app.core.analyzer import CodeAnalyzer
from app.core.auth import initialize_firebase, verify_token
from app.core.chat_context import (
    build_file_context,
    build_general_context,
    guess_language,
    read_repo_file,
    resolve_repo_file,
    select_context_files,
)
from app.core.llm import LLMError, get_llm_client
from app.core.prompts import load_prompt


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

# Core services
analyzer = CodeAnalyzer()


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
async def analyze_repo_stream(url: str = Query(..., description="Public GitHub repository URL"), user: dict = Depends(verify_token)):
    progress_queue: "queue.Queue[str]" = queue.Queue()

    def progress_callback(message: str) -> None:
        progress_queue.put(message)

    async def drain(status: str) -> AsyncIterator[dict[str, str]]:
        while True:
            try:
                message = progress_queue.get_nowait()
            except queue.Empty:
                return
            yield {"data": json.dumps({"status": status, "message": message})}

    async def event_generator():
        yield {"data": json.dumps({"status": "starting", "message": f"Initializing analysis for {url}"})}
        await asyncio.sleep(0.3)

        try:
            yield {"data": json.dumps({"status": "cloning", "message": "Cloning repository (shallow)..."})}

            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = loop.run_in_executor(pool, lambda: analyzer.analyze_repo(url, progress_callback=progress_callback))

                while not future.done():
                    await asyncio.sleep(0.3)
                    async for event in drain("analyzing"):
                        yield event

                results = await asyncio.wrap_future(future)

            async for event in drain("analyzing"):
                yield event

            yield {
                "data": json.dumps(
                    {"status": "completed", "message": f"Analysed {len(results['files'])} files", "results": results}
                )
            }
        except Exception as error:  # noqa: BLE001 - reported to the client as an event
            print(f"Analysis failed for {url}: {error}", flush=True)
            yield {"data": json.dumps({"status": "error", "message": str(error)})}

    return EventSourceResponse(event_generator())


class ChatRequest(BaseModel):
    repo_id: str
    query: str


class RepoFileRequest(BaseModel):
    repo_id: str
    file_path: str


def _chat_prompt(query: str, context: str, used_files: list[str]) -> list[dict[str, str]]:
    template = load_prompt("chat_prompt.txt")
    grounding = ""
    if used_files:
        grounding = "\nFiles you were given: " + ", ".join(used_files)
    user_prompt = (
        f"{template}\n\nRepository evidence:\n{context}{grounding}\n\n"
        f"Question: {query}\n\nAnswer from the evidence above, citing file paths."
    )
    return [
        {
            "role": "system",
            "content": (
                "You are CodeLens, an assistant that answers questions about one specific codebase using "
                "only the evidence you were given. Say so plainly when the evidence is missing."
            ),
        },
        {"role": "user", "content": user_prompt},
    ]


def _chat_context(snapshot: dict, query: str) -> tuple[str, list[str]]:
    matched_files = select_context_files(snapshot, query)
    if matched_files:
        return build_file_context(snapshot, matched_files, query)
    return build_general_context(snapshot), []


@app.post("/chat")
async def chat(request: ChatRequest, user: dict = Depends(verify_token)):
    snapshot = analyzer.ingestion.load_snapshot(request.repo_id)
    if not snapshot:
        return {"answer": "I could not find a stored snapshot for that repository yet. Run an analysis first, then ask again."}

    llm = get_llm_client()
    if not llm.configured:
        return {"error": "Model access is not configured on this server (NEBIUS_API_KEY is missing).", "answer": ""}

    context, used_files = _chat_context(snapshot, request.query)
    try:
        result = await llm.chat(_chat_prompt(request.query, context, used_files), role="fast")
    except LLMError as error:
        return {"error": str(error), "answer": ""}

    return {
        "answer": result.text,
        "model": result.model,
        "usage": result.usage_dict(),
        "sources": used_files,
    }


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest, user: dict = Depends(verify_token)):
    """Stream an answer as Server-Sent Events using the fast model."""
    snapshot = analyzer.ingestion.load_snapshot(request.repo_id)
    if not snapshot:
        return EventSourceResponse(
            _error_events("I could not find a stored snapshot for that repository yet. Run an analysis first.")
        )

    llm = get_llm_client()
    if not llm.configured:
        return EventSourceResponse(_error_events("Model access is not configured on this server (NEBIUS_API_KEY is missing)."))

    context, used_files = _chat_context(snapshot, request.query)
    messages = _chat_prompt(request.query, context, used_files)

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        yield {"event": "sources", "data": json.dumps({"sources": used_files, "model": llm.model_for("fast")})}
        try:
            async for delta in llm.chat_stream(messages, role="fast"):
                yield {"event": "delta", "data": json.dumps({"text": delta})}
            yield {"event": "done", "data": json.dumps({"ok": True})}
        except LLMError as error:
            yield {"event": "error", "data": json.dumps({"message": str(error)})}

    return EventSourceResponse(event_generator())


async def _error_events(message: str) -> AsyncIterator[dict[str, str]]:
    yield {"event": "error", "data": json.dumps({"message": message})}
    yield {"event": "done", "data": json.dumps({"ok": False})}


@app.post("/repo/file")
async def repo_file(request: RepoFileRequest, user: dict = Depends(verify_token)):
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
