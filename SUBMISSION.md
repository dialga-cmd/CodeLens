# CodeLens — hackathon submission

Everything Devpost asks for, in the order it is asked for. Fields that still
need a value after deployment are marked `TODO` with the exact value to paste.

---

## 1. Project basics

| Field | Value |
| --- | --- |
| Project name | **CodeLens** |
| Tagline | Read an unfamiliar codebase by its own numbers — dependency graph, hotspots, grounded findings, and patches that are checked against the real repo before you see them. |
| Track | **Best Apps and Agents** |
| Repository | `https://github.com/dialga-cmd/CodeLens` |
| Licence | MIT, © 2026 Aditya Raj (`LICENSE`) |
| Language / stack | Python 3.11 · FastAPI · Tree-sitter · Next.js 16 (App Router, TypeScript, static export) |

## 2. Links to fill in after deployment

| Field | Value |
| --- | --- |
| **Live demo URL** | `TODO` — see §6 for how to produce it and what to check |
| **Video URL (YouTube, < 3 min)** | `TODO` — see §7 for the shot list |
| Demo repository to analyse live | `https://github.com/pallets/flask` (also `pallets/itsdangerous`, `colinhacks/zod`) |

## 3. What is it

Paste a public GitHub URL. CodeLens clones it, parses every source file with
Tree-sitter, resolves the imports into a real dependency graph, measures
complexity / fan-in / fan-out / churn, runs 18 deterministic security rules,
and ranks the files that are expensive to change. It then does the part a
scanner cannot:

* **The heavyweight model triages every candidate finding** and says why each
  one is or is not real. On Flask, 74 pattern matches became **4 counted
  findings and 70 dismissals, each with a written reason**.
* **Tavily grounds the survivors** in live sources — NIST on the SHA-1 finding,
  OWASP and CWE-95 on the `exec` findings — and the links are in the report.
* **The model drafts a patch, and `git apply --check` decides whether it is
  real.** A patch that does not apply is shown as a failure with git's own
  error, never as a suggestion.
* **Chat answers questions about the analysis with tools**, and every answer
  lists the files it is based on.

The design rule throughout: a measurement is labelled as a measurement, a model
opinion is labelled as an opinion, and anything unverified says so.

## 4. NVIDIA models used

Both models are NVIDIA open-weight models served by Nebius Token Factory, and
both were verified present on this account with `GET /v1/models` before being
hardcoded (25 models available, 4 of them Nemotron).

| Role | Model ID | Why this one |
| --- | --- | --- |
| Analysis: triage, architecture, remediation, patches | `nvidia/Nemotron-3-Ultra-550b-a55b` | The 550B MoE model. Triage is the step where a wrong verdict is expensive, so this is where the budget goes. |
| Chat | `nvidia/Nemotron-3_5-Lightning` | Answers in 1.8–4.3 s, which is what makes chat feel like a tool rather than a wait. |

There is no OpenAI, Anthropic or Google model in the codebase, and no other
provider's SDK: `server/app/core/llm.py` is the only file that constructs a
client, and it constructs exactly one, pointed at
`https://api.tokenfactory.nebius.com/v1/`.

## 5. What calls the Nebius API at runtime

Every analysis is 2–9 requests to Token Factory, with the token count reported in
the response. A measured Flask run:

```
Model triage: asking nvidia/Nemotron-3-Ultra-550b-a55b to review the candidates.
Reviewing 74 candidates in 7 batches with nvidia/Nemotron-3-Ultra-550b-a55b...
Model triage: 4 confirmed, 70 dismissed, 0 further issues reported.
Asking the model for an architecture summary.
Writing remediation for 4 findings with nvidia/Nemotron-3-Ultra-550b-a55b...
```

9 calls, 43 393 tokens, 36.6 s wall clock, and the same numbers visible in the
response body (`llm_usage` on every result). Chat adds one more per question.

**Bonus criterion — a real Tavily call.** Not a mock and not a fallback: 13
live `POST https://api.tavily.com/search` requests during the Flask analysis,
three of which produced the sources attached to findings. `research_queries: 13`
in the result body is the count. Without `TAVILY_API_KEY` the app still runs and
says grounding was skipped.

## 6. Deploying, and what to check before pasting the URL

Full procedure in [`docs/DEPLOY_NEBIUS.md`](docs/DEPLOY_NEBIUS.md). The short
version: deploy `server/` on Nebius AI Cloud with `NEBIUS_API_KEY` and
`TAVILY_API_KEY` set, then `client/` on Firebase Hosting with
`NEXT_PUBLIC_API_URL` pointed at it.

Six checks to run **against the deployed URL**, not against localhost, and paste
the results into `docs/TEST_REPORT.md` §10 as they pass:

```bash
API=<your deployed api url>

curl -s $API/health          # llm.configured: true, research.configured: true
curl -s $API/api/config      # auth.mode: "guest", every capability true
curl -s $API/api/demo/flask  # 200, ~367 KB, no login and no token
curl -sN "$API/analyze/stream?url=https://github.com/pallets/flask&refresh=true"
                             # ~37 s, then the full results object
curl -sN -X POST $API/chat/stream -H 'Content-Type: application/json' \
  -d '{"repo_id":"<repo_id>","query":"which file is the biggest hotspot?"}'
                             # delta events, then done
```

Then open the client URL in a browser and confirm: the **Try a demo repo**
button opens the stored Flask report with no login, an error state appears when
you ask for a repository that does not exist, and the layout is usable at 375 px
wide. Those three are the parts this environment cannot check — there is no
browser here.

## 7. Video script — 2 min 40 s

No voice-over over music; screen recording with captions, silent or with a
narration track recorded from this script.

| Time | Shot | Caption / line |
| --- | --- | --- |
| 0:00–0:12 | Landing page | "Paste a GitHub URL. CodeLens clones it, parses it, and tells you what is expensive to change." |
| 0:12–0:25 | Paste `github.com/pallets/flask`, watch the progress stream | Live progress, real repository: "96 files parsed, 221 imports resolved, 8 hotspots ranked." |
| 0:25–0:50 | Scroll the security panel | "74 pattern matches. The model triaged every one: 4 confirmed, 70 dismissed — each with a reason." |
| 0:50–1:05 | Open a confirmed finding | Show the file, the line, the severity, and the NIST link from Tavily. "The source is a live search, not a remembered CVE number." |
| 1:05–1:25 | Open a dismissed finding | "This `weak_hash` match is inside an HMAC. The model dismissed it, and here is why: SHA-1 in an HMAC is not the weakness this rule was written for." |
| 1:25–1:50 | Ask Fix Advisor for a patch on `sessions.py:277` | Show `git apply --check passed`. "The patch is checked against the real repository before you see it." |
| 1:50–2:15 | Ask chat "which file has the worst hotspot score and why?" | Answer arrives in ~2 s, cites `src/flask/app.py`, score 0.751, with the file list it used. |
| 2:15–2:35 | Reload the page and press **Try a demo repo** | "No account, no key. Two full analyses ship with the project." |
| 2:35–2:40 | End card | "CodeLens — FastAPI, Tree-sitter, nvidia/Nemotron-3-Ultra-550b-a55b on Nebius Token Factory, live Tavily grounding. MIT licence." |

## 8. Pre-existing project — what changed

The repository predates the hackathon (single commit dated 2026-06-20). Per the
rules, it has been significantly updated. The full account is in the README's
[What changed](README.md#what-changed) section; in short:

* Every LLM call moved to Nebius Token Factory behind one client with retries,
  backoff, JSON repair and streaming.
* Real static analysis replaced the placeholders: Tree-sitter parsing, a
  resolved import graph, hotspot ranking from complexity + churn + fan-in, and
  18 deterministic security rules.
* Findings are triaged by the model instead of being reported raw, and
  dismissals are counted separately so a dismissed pattern is never presented
  as a vulnerability.
* Live Tavily grounding with visible sources.
* Fix Advisor producing patches validated with `git apply --check`, plus
  tool-calling chat over the analysis.
* Durable storage, background jobs with a progress stream, rate and size limits,
  strict URL validation and path-traversal protection.
* Guest mode, `/health`, `/docs`, env-configurable CORS, a Dockerfile, and
  deployment docs.
* A rewritten client with loading / empty / error states, a mobile layout,
  accessible labels, and two committed demo analyses.

## 9. Judging criteria, and where the evidence is

| Criterion | What to point at | Evidence |
| --- | --- | --- |
| **Tech implementation** | `server/app/core/llm.py`, `fix_advisor.py`, `analyzer.py` | 237 unit tests; a measured 36.6 s end-to-end analysis on 9 concurrent model calls; a diff the model wrote, re-anchored to the real file and confirmed by git |
| **Design** | `client/src/app/results/page.tsx`, the progress stream | Every panel separates measurement from model opinion; dismissed findings are visible rather than hidden; unverified findings are labelled |
| **Impact** | triage + grounding + checked patches | 70 of 74 raw matches removed as false positives with written reasons; a judge does not have to read 70 wrong alerts to find the 4 real ones |
| **Idea quality** | the split between scanner and model | A model that must disagree with a specific, located finding produces better triage than one asked to review a repository; and `git apply --check` makes the model's patch falsifiable |

`docs/TEST_REPORT.md` has the full trace, including the thirteen bugs that only
appeared once it was run against the live API and what fixed each one.

## 10. Honest limitations

Stated in the README too, because a judge should not have to discover these:

* Analysis quality is bounded by file size — a repository over 250 source files
  is truncated, and the stream says so.
* The security rules are 18 deterministic patterns, not a full SAST. On Flask
  they fire 74 times and 4 survive triage; the number that matters is the 4, and
  it is not a claim that Flask has four vulnerabilities.
* Fix Advisor succeeds on the first attempt most of the time, not always. When
  a patch does not apply, the UI says so and shows git's error.
* Architecture extraction is model-dependent. When the answer is unusable the
  report says `extracted: false` instead of showing an empty panel as if it had
  worked.
* Stored demo analyses have no clone behind them, so file viewing, chat tools
  and Fix Advisor report `demo.live_required_for` on those.
* Nothing in this document's §2 links can be filled until the deployment in §6
  is done. That is the one gap left.