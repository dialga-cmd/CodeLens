from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
import os
import json
import asyncio
import anyio
import re
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app.core.analyzer import CodeAnalyzer
from app.core.auth import initialize_firebase, verify_token


app = FastAPI(title="CodeLens API")
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

@app.get("/health")
async def health_check():
    return {
        "status": "server on, redirect to dashboard",
        "redirect": "/dashboard",
    }

# Initialize Core Services
analyzer = CodeAnalyzer()


def _safe_read_file(repo_path: str, relative_path: str, max_bytes: int | None = 12000) -> str:
    if not repo_path or not relative_path:
        return ""

    full_path = os.path.normpath(os.path.join(repo_path, relative_path))
    repo_root = os.path.normpath(repo_path)
    if not full_path.startswith(repo_root):
        return ""

    if not os.path.exists(full_path) or not os.path.isfile(full_path):
        return ""

    try:
        with open(full_path, "r", errors="ignore") as handle:
            return handle.read() if max_bytes is None else handle.read(max_bytes)
    except Exception:
        return ""


def _resolve_repo_file(snapshot: dict, relative_path: str) -> tuple[str, str]:
    repo_path = snapshot.get("repo_path", "")
    if not repo_path or not relative_path:
        return "", ""

    normalized = os.path.normpath(relative_path).lstrip(os.sep)
    full_path = os.path.normpath(os.path.join(repo_path, normalized))
    repo_root = os.path.normpath(repo_path)
    if not full_path.startswith(repo_root):
        return "", ""

    if not os.path.exists(full_path) or not os.path.isfile(full_path):
        return "", ""

    try:
        return full_path, os.path.relpath(full_path, repo_path)
    except Exception:
        return "", ""


def _build_graph_neighbors(snapshot: dict) -> dict:
    neighbors = {}
    for link in snapshot.get("graph", {}).get("links", []):
        source = str(link.get("source", ""))
        target = str(link.get("target", ""))
        if not source or not target:
            continue
        neighbors.setdefault(source, set()).add(target)
        neighbors.setdefault(target, set()).add(source)
    return neighbors


def _resolve_file_candidates(snapshot: dict, query: str):
    files = snapshot.get("files", [])
    lowered = query.lower()

    exact_path_matches = [f for f in files if f.get("file_path") and f["file_path"].lower() in lowered]
    if exact_path_matches:
        return exact_path_matches

    basename_matches = []
    for f in files:
        file_path = str(f.get("file_path", ""))
        basename = os.path.basename(file_path).lower()
        if basename and basename in lowered:
            basename_matches.append(f)

    if basename_matches:
        return basename_matches

    tokens = [token.strip(".,:;!?()[]{}\"'").lower() for token in query.split()]
    heuristic_matches = []
    for f in files:
        file_path = str(f.get("file_path", "")).lower()
        basename = os.path.basename(file_path)
        if any(token == basename.lower() or token in file_path for token in tokens if len(token) > 2):
            heuristic_matches.append(f)

    return heuristic_matches


def _query_terms(query: str) -> list[str]:
    stop_words = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "these",
        "those",
        "please",
        "can",
        "you",
        "tell",
        "about",
        "show",
        "read",
        "all",
        "file",
        "files",
        "project",
        "repo",
        "repository",
        "my",
        "different",
        "please",
        "need",
        "want",
        "access",
        "look",
        "see",
    }
    tokens = []
    for raw in re.split(r"[^a-zA-Z0-9_.-]+", query.lower()):
        token = raw.strip("._-")
        if len(token) < 2 or token in stop_words:
            continue
        if token not in tokens:
            tokens.append(token)
    return tokens


def _is_broad_query(query: str) -> bool:
    lowered = query.lower()
    broad_markers = ["all files", "related", "everything", "entire", "broad", "overview", "analyze", "read all", "scan", "chat", "conversation"]
    return any(marker in lowered for marker in broad_markers)


def _guess_language(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    language_map = {
        "py": "python",
        "js": "javascript",
        "jsx": "javascript",
        "ts": "typescript",
        "tsx": "typescript",
        "mjs": "javascript",
        "cjs": "javascript",
        "json": "json",
        "md": "markdown",
        "toml": "toml",
        "yml": "yaml",
        "yaml": "yaml",
        "css": "css",
        "html": "html",
    }
    return language_map.get(ext, ext or "text")


def _collect_repo_file_paths(repo_path: str) -> list[str]:
    allowed_exts = {
        "py",
        "js",
        "jsx",
        "ts",
        "tsx",
        "mjs",
        "cjs",
        "json",
        "md",
        "txt",
        "toml",
        "yml",
        "yaml",
        "css",
        "html",
        "env",
    }
    skip_dirs = {".git", "node_modules", "__pycache__", ".next", "dist", "build", "coverage", "venv", ".venv"}
    collected = []

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [directory for directory in dirs if directory not in skip_dirs]
        for file_name in files:
            full_path = os.path.join(root, file_name)
            try:
                if os.path.getsize(full_path) > 200_000:
                    continue
            except Exception:
                continue

            ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
            is_env_example = file_name.startswith(".env")
            if ext in allowed_exts or file_name.lower() in {"readme", "readme.md", "license", "makefile"} or is_env_example:
                collected.append(os.path.relpath(full_path, repo_path))

    return collected


def _score_file_for_query(file_info: dict, query_terms: list[str]) -> int:
    file_path = str(file_info.get("file_path", "")).lower()
    basename = os.path.basename(file_path)
    dirname = os.path.dirname(file_path)
    score = 0

    for term in query_terms:
        if term == basename:
            score += 8
        if term in basename:
            score += 6
        if term in file_path:
            score += 4
        if term in dirname:
            score += 2

    if basename in {"readme.md", "package.json", "requirements.txt", "pyproject.toml"}:
        score += 1

    return score


def _select_context_files(snapshot: dict, query: str, max_files: int = 10) -> list[dict]:
    files = list(snapshot.get("files", []))
    repo_path = snapshot.get("repo_path", "")
    if repo_path and os.path.isdir(repo_path):
        existing_paths = {str(file_info.get("file_path", "")) for file_info in files}
        for file_path in _collect_repo_file_paths(repo_path):
            if file_path not in existing_paths:
                files.append(
                    {
                        "file_path": file_path,
                        "language": _guess_language(file_path),
                        "imports": [],
                        "complexity": 0,
                        "functions": [],
                        "classes": [],
                        "vulnerabilities": 0,
                    }
                )

    query_terms = _query_terms(query)
    broad = _is_broad_query(query)

    scored = []
    for file_info in files:
        score = _score_file_for_query(file_info, query_terms)
        if score > 0:
            scored.append((score, file_info))

    if not scored and broad:
        special_files = snapshot.get("special_files", []) or []
        special_paths = {str(item.get("file_path", "")) for item in special_files}
        for file_info in files:
            if str(file_info.get("file_path", "")) in special_paths:
                scored.append((1, file_info))

    scored.sort(key=lambda item: (-item[0], str(item[1].get("file_path", ""))))
    selected = []
    seen = set()

    limit = max_files if broad else min(max_files, 6)
    for _, file_info in scored:
        file_path = str(file_info.get("file_path", ""))
        if not file_path or file_path in seen:
            continue
        seen.add(file_path)
        selected.append(file_info)
        if len(selected) >= limit:
            break

    if not selected:
        selected = files[: min(limit, len(files))]

    return selected


def _is_general_query(query: str) -> bool:
    lowered = query.lower()
    general_markers = ["overview", "summary", "what does", "how does", "explain", "architecture", "repo", "project", "security", "dependency", "hotspot", "readme"]
    return any(marker in lowered for marker in general_markers)


def _build_file_context(snapshot: dict, targets: list[dict], query: str) -> tuple[str, list[str]]:
    repo_path = snapshot.get("repo_path", "")
    neighbors = _build_graph_neighbors(snapshot)
    files_by_path = {str(f.get("file_path", "")): f for f in snapshot.get("files", [])}

    broad = _is_broad_query(query)
    max_files = 12 if broad else 6
    selected_targets = targets[:max_files]

    context_paths = []
    sections = []
    if len(selected_targets) > 1:
        sections.append(
            f"Matched {len(selected_targets)} files for this request. Reading the strongest matches and their nearby context instead of forcing a single file path."
        )

    total_chars = 0
    max_total_chars = 32000 if broad else 18000

    for target in selected_targets:
        target_path = str(target.get("file_path", ""))
        if not target_path or target_path in context_paths:
            continue

        context_paths.append(target_path)
        sections.append(f"\nFile: {target_path}")
        target_content = _safe_read_file(repo_path, target_path)
        if target_content:
            clipped = target_content[:6000]
            sections.append(clipped)
            total_chars += len(clipped)

        related_paths = []
        for path in sorted(neighbors.get(target_path, set())):
            if path in files_by_path and path != target_path:
                related_paths.append(path)

        same_dir = os.path.dirname(target_path)
        for f in snapshot.get("files", []):
            path = str(f.get("file_path", ""))
            if path != target_path and os.path.dirname(path) == same_dir:
                related_paths.append(path)

        unique_related = []
        seen = set(context_paths)
        for path in related_paths:
            if path not in seen:
                seen.add(path)
                unique_related.append(path)

        for path in unique_related[:2]:
            if path in context_paths or total_chars >= max_total_chars:
                continue
            related_content = _safe_read_file(repo_path, path)
            if related_content:
                clipped = related_content[:4000]
                context_paths.append(path)
                sections.append(f"\nRelated file: {path}\n{clipped}")
                total_chars += len(clipped)

        if total_chars >= max_total_chars:
            break

    return "\n".join(sections), context_paths


def _build_general_context(snapshot: dict) -> str:
    parts = [
        f"Repository URL: {snapshot.get('repo_url', '')}",
        f"Total files: {len(snapshot.get('files', []))}",
    ]

    tech_stack = snapshot.get("tech_stack", {}) or {}
    if tech_stack:
        parts.append(f"Tech stack: {json.dumps(tech_stack)[:3000]}")

    dependencies = snapshot.get("ai_dependencies", {}) or {}
    if dependencies:
        parts.append(f"Dependencies: {json.dumps(dependencies)[:3000]}")

    hotspots = snapshot.get("hotspots", []) or []
    if hotspots:
        parts.append(f"Hotspots: {json.dumps(hotspots[:10])[:3000]}")

    vulnerabilities = snapshot.get("vulnerabilities", []) or []
    if vulnerabilities:
        parts.append(f"Security findings: {json.dumps(vulnerabilities[:10])[:3000]}")

    special_files = snapshot.get("special_files", []) or []
    for file_info in special_files[:5]:
        parts.append(f"\nFile: {file_info.get('file_path')}\n{file_info.get('content', '')[:3000]}")

    top_files = snapshot.get("files", [])[:8]
    if top_files:
        parts.append("\nRepository structure:")
        parts.extend([f"- {f.get('file_path')} ({f.get('language')})" for f in top_files])

    return "\n".join(parts)

@app.get("/analyze/stream")
async def analyze_repo_stream(url: str, user: dict = Depends(verify_token)):
    import queue
    progress_queue = queue.Queue()

    def progress_callback(message: str):
        progress_queue.put(message)

    async def event_generator():
        yield {"data": json.dumps({"status": "starting", "message": f"Initializing analysis for {url}"})}
        await asyncio.sleep(0.5)

        try:
            yield {"data": json.dumps({"status": "cloning", "message": "Cloning repository to secure volume..."})}

            # Run the heavy analysis in a separate thread, with progress callback
            import concurrent.futures
            loop = asyncio.get_event_loop()

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = loop.run_in_executor(pool, lambda: analyzer.analyze_repo(url, progress_callback=progress_callback))

                # Poll for progress messages while waiting for completion
                while not future.done():
                    await asyncio.sleep(0.3)
                    # Drain all progress messages
                    while not progress_queue.empty():
                        try:
                            msg = progress_queue.get_nowait()
                            yield {"data": json.dumps({"status": "analyzing", "message": msg})}
                        except queue.Empty:
                            break

                # Get the result (may raise if the task failed)
                results = await asyncio.wait_for(asyncio.wrap_future(future), timeout=120)

            # Drain any remaining progress messages
            while not progress_queue.empty():
                try:
                    msg = progress_queue.get_nowait()
                    yield {"data": json.dumps({"status": "analyzing", "message": msg})}
                except queue.Empty:
                    break

            yield {"data": json.dumps({"status": "parsing", "message": f"Successfully analyzed {len(results['files'])} files"})}
            await asyncio.sleep(0.5)

            yield {"data": json.dumps({"status": "completed", "results": results})}
        except Exception as e:
            import traceback
            traceback.print_exc()
            yield {"data": json.dumps({"status": "error", "message": str(e)})}

    return EventSourceResponse(event_generator())

class ChatRequest(BaseModel):
    repo_id: str
    query: str


class RepoFileRequest(BaseModel):
    repo_id: str
    file_path: str

@app.post("/chat")
async def chat(request: ChatRequest, user: dict = Depends(verify_token)):
    snapshot = analyzer.ingestion.load_snapshot(request.repo_id)
    if not snapshot:
        return {"answer": "I could not find a stored repo snapshot for that repository yet. Run an analysis first, then ask again."}

    matched_files = _select_context_files(snapshot, request.query)
    if matched_files:
        context_str, matched_paths = _build_file_context(snapshot, matched_files, request.query)
    else:
        context_str = _build_general_context(snapshot)
        matched_paths = []

    # 2. Call LLM using NIM API
    nim_keys_env = os.getenv("NIM_API_KEYS", os.getenv("NIM_API_KEY", ""))
    nim_keys = [k.strip() for k in nim_keys_env.split(",") if k.strip()]

    if not nim_keys:
        return {"answer": "Error: No NIM API keys configured in NIM_API_KEYS."}

    prompt = f"""
    You are CodeLens AI. Answer the user's question about the following codebase context.
    Use the provided repo structure, linked files, hotspots, dependencies, security findings, and direct file contents to be specific.
    If multiple files are provided, synthesize across them instead of asking the user to choose one.
    If the query references an area like chat, UI, auth, or data flow, review the strongest matching files and summarize how they work together.
    If the query is general, summarize the repository from the provided context.
    Cite file paths when useful, and mention when you could not inspect a file directly.

    Context:
    {context_str}

    Question: {request.query}
    """

    last_error = "No NIM API keys worked."

    # Try NIM Keys
    try:
        from openai import AsyncOpenAI
        for key in nim_keys:
            try:
                client = AsyncOpenAI(api_key=key, base_url="https://integrate.api.nvidia.com/v1")
                response = await client.chat.completions.create(
                    model="nvidia/llama-3.3-nemotron-super-49b-v1",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3
                )
                return {"answer": response.choices[0].message.content}
            except Exception as e:
                last_error = f"NIM error: {str(e)}"
                continue
    except ImportError:
        last_error = "openai package is not installed."

    return {"answer": f"Error: All NIM API attempts failed. Last error: {last_error}"}


@app.post("/repo/file")
async def repo_file(request: RepoFileRequest, user: dict = Depends(verify_token)):
    snapshot = analyzer.ingestion.load_snapshot(request.repo_id)
    if not snapshot:
        return {"error": "Repository snapshot not found."}

    full_path, resolved_path = _resolve_repo_file(snapshot, request.file_path)
    if not full_path:
        return {"error": "File not found in repository."}

    content = _safe_read_file(snapshot.get("repo_path", ""), resolved_path, max_bytes=None)
    if content == "":
        return {"error": "File could not be read."}

    return {
        "repo_id": request.repo_id,
        "file_path": resolved_path,
        "content": content,
        "language": _guess_language(resolved_path),
        "size": len(content),
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)