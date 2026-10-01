# Test report

Every claim this project makes about its own behaviour is supposed to be
traceable to a request in this file. Anything not recorded here is an intention,
not a result.

**Status: not yet run.** The end-to-end pass needs a `NEBIUS_API_KEY` and a
`TAVILY_API_KEY` in `server/.env`, and nothing in this repository should be
published as working until it has been.

## What has been verified so far

Everything below was run against the local checkout, with no keys present, and is
reproducible right now.

| # | Check | Command | Result |
| --- | --- | --- | --- |
| 1 | Server unit suite | `cd server && pytest tests/ -q` | 172 passed |
| 2 | Client types | `cd client && npx tsc --noEmit` | clean |
| 3 | Client production build | `cd client && npm run build` | clean, 6 static routes |
| 4 | API reachable | `GET /health` | 200 |
| 5 | Interactive docs | `GET /docs` | 200 |
| 6 | Stored demos served | `GET /api/demo`, `GET /api/demo/flask`, `GET /api/demo/zod` | 200 |
| 7 | CORS preflight from the client origin | `OPTIONS /health` with `Origin: http://localhost:3000` | `access-control-allow-origin: http://localhost:3000` |
| 8 | Demo report contents | `GET /api/demo/flask` | 96 files, 195 edges, 8 hotspots, 74 findings |

Note what is *not* on this list: nothing has been run against Token Factory or
Tavily yet, and no model-dependent behaviour is claimed anywhere until it is.

## Still to verify

The following are the specific open questions, kept in sync with
[PLATFORM_NOTES.md](PLATFORM_NOTES.md#5-open-questions--verified-live-in-phase-5-before-any-claim-is-made-public).
Each row is filled in with the request that produced it, not with a summary.

| # | Question | How it will be checked | Result |
| --- | --- | --- | --- |
| 1 | Does the default Token Factory host serve both the US and EU models, or must `NEBIUS_BASE_URL` be regional? | one call per model against the default host | _pending_ |
| 2 | Are the configured heavy and fast model IDs present for this account? | `GET /v1/models` | _pending_ |
| 3 | Do `response_format` and function calling work on both models? | one chat completion per model | _pending_ |
| 4 | What do the model-backed features actually return? | `/analyze/stream`, `/chat/stream`, `/api/fix` on a small repository | _pending_ |
| 5 | Does a generated diff apply? | `POST /api/fix`, `applies` field | _pending_ |
| 6 | Does Tavily return sources for a real dependency question? | one research run, sources recorded | _pending_ |
| 7 | What is the wall-clock and token cost of one analysis? | `llm_usage` from the response | _pending_ |
| 8 | Does a public deployment work without a login? | full pass on the deployed URL, no account | _pending_ |

## How to reproduce

```bash
# with keys in server/.env
cd server
uvicorn main:app --port 8000
```

Then, in another terminal:

```bash
API=http://localhost:8000

curl -s $API/health | python3 -m json.tool
curl -s $API/api/models | python3 -m json.tool

# a small repository, end to end, with the live progress stream
curl -N "$API/analyze/stream?url=https://github.com/pallets/itsdangerous"

# which model the pipeline will use, and whether it is available
curl -s $API/analyze/inspect | python3 -m json.tool
```

Each result, including the raw output that produced it, gets pasted below with
the date it was taken.
