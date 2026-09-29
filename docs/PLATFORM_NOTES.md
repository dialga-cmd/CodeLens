# Platform Notes — Nebius Token Factory, NVIDIA Nemotron, Tavily, Nebius AI Cloud

Everything in this file was read from official documentation or official public catalog
endpoints on **2026-09-29**. Nothing here is guessed. Every claim lists the source it came from,
and every value the application depends on is overridable through an environment variable.

These notes are the reference used while building CodeLens. They also explain *why* the
heavy/fast model split exists and which model IDs are actually usable.

---

## 1. Nebius Token Factory (text generation)

### 1.1 Connection

| Item | Value | Source |
| --- | --- | --- |
| Base URL | `https://api.tokenfactory.nebius.com/v1/` | [Quickstart](https://docs.tokenfactory.nebius.com/quickstart.md) |
| Auth header | `Authorization: Bearer $NEBIUS_API_KEY` | [API reference](https://docs.tokenfactory.nebius.com/api-reference/introduction.md) |
| Protocol | OpenAI-compatible (`/chat/completions`, `tools`, `tool_choice`, `response_format`, `stream`) | [API reference](https://docs.tokenfactory.nebius.com/api-reference/introduction.md) |
| Python SDK | `openai` package with a custom `base_url` | Quickstart examples |
| Key | `NEBIUS_API_KEY` from the [Token Factory console](https://tokenfactory.nebius.com/) | Quickstart |

Because the API is OpenAI-compatible, the client is the standard `openai` Python SDK with
`base_url` pointed at Token Factory. There is no vendor-specific SDK to learn.

`https://api.studio.nebius.ai/v1/models` (the older Studio AI host) returns `401` for a
Token Factory key — it is not the host to use. The current host is `api.tokenfactory.nebius.com`.

### 1.2 Listing models

`GET /v1/models` (optional `?verbose=true`) returns an OpenAI-style list:

```jsonc
{ "object": "list", "data": [ { "id": "...", "name": "...", "created": 1717511223, "object": "model", "description": "...", "context_length": ..., "status": "..." } ] }
```

`verbose=true` adds per-model details such as architecture, quantization, pricing,
supported features and serving regions. Source: [List models](https://docs.tokenfactory.nebius.com/api-reference/models/list-models.md).

CodeLens uses this endpoint at startup (and on demand via `GET /api/models`) as the
authoritative list of model IDs available to the account, rather than hardcoding a
catalogue. A catalogue is only used to pick sensible defaults.

### 1.3 Function calling

Standard OpenAI-style tools are supported: `tools=[{"type":"function","function":{...}}]` and
`tool_choice` of `"auto"` or an explicit function object.
Source: [Function calling](https://docs.tokenfactory.nebius.com/ai-models-inference/function-calling.md).
All four NVIDIA Nemotron models used by CodeLens are tagged `function_calling`.

### 1.4 Structured output (JSON)

`response_format` is supported in two modes
([Structured output & JSON](https://docs.tokenfactory.nebius.com/ai-models-inference/json.md)):

* `{"type": "json_object"}` — forces a valid, schema-free JSON object.
* `{"type": "json_schema", "json_schema": {...}}` — forces JSON following a
  [JSON Schema](https://json-schema.org/specification) document. The documented recommendation is
  to also restate the schema in the prompt.

Two operational consequences used by CodeLens:

* `choices[0].message.refusal` may be populated instead of `content`, and must be handled.
* A `400` is returned for an unsupported schema, so every LLM call has a robust
  parse-and-repair path plus a retry that drops the schema instead of losing the result.

### 1.5 Rate limits

Usage is evaluated in **rolling 15-minute buckets** and the limit moves automatically
([Rate limits](https://docs.tokenfactory.nebius.com/ai-models-inference/rate-limits.md)):

* **Scale up** when average usage over 15 min is >= 80% of the current limit: next window `x 1.2`.
* **Scale down** when average usage over 15 min is <= 50%: next window `/ 1.5`.
* **Hard ceiling** `20x` the base allocation (Enterprise beyond that).

Exceeding the active limit returns **HTTP 429** with `Retry-After`. Headers worth reading:

`x-ratelimit-limit-requests`, `x-ratelimit-limit-tokens`,
`x-ratelimit-remaining-requests`, `x-ratelimit-remaining-tokens`,
`x-ratelimit-reset-requests`, `x-ratelimit-reset-tokens`,
`x-ratelimit-dynamic-scale-requests`, `x-ratelimit-dynamic-scale-tokens`,
`x-ratelimit-dynamic-period-remaining`, `x-ratelimit-dynamic-period-usage-requests`,
`x-ratelimit-dynamic-period-usage-tokens`, `x-ratelimit-over-limit`, `Retry-After`.

CodeLens therefore implements **bounded retries with exponential backoff that honours
`Retry-After`**, plus a small client-side concurrency limiter, so a burst of parallel
analysis steps degrades instead of failing the whole run.

### 1.6 Model flavors: Base and Fast

Two flavors exist per model ([Inference overview](https://docs.tokenfactory.nebius.com/ai-models-inference/overview.md)):

* **Base** — default, optimised for throughput/cost.
* **Fast** — smaller batches, more compute per request, speculative decoding; lower latency.

> "To use the Fast flavor, append `-fast` to the model name in the API."

Both flavors return **identical model outputs**; only latency, token pricing and
optimisation level differ. The published catalog snapshot lists one flavor per Nemotron
model, so CodeLens does **not** hardcode a `-fast` ID. Whether `<model>-fast` exists for the
chosen Nemotron IDs is verified live against `GET /v1/models` (see Open Questions).

---

## 2. NVIDIA Nemotron models on Token Factory

Source of truth: the official public catalog API
`https://tokenfactory.nebius.com/api/public/models_info` (human-readable:
`https://tokenfactory.nebius.com/model-catalog.md`). All four NVIDIA models it lists, with
values exactly as published:

| Model ID | Params / arch | `max_model_len` | Region | Throughput | Price in/out per 1M tok | Quantization | Capabilities |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `nvidia/Nemotron-3-Ultra-550b-a55b` | 550B A55B MoE | 1,048,576 (1M) | `us-central1` (US) | 523 tok/s | $1.00 / $3.00 | fp4 | function calling, reasoning, code |
| `nvidia/nemotron-3-super-120b-a12b` | 120B A12B MoE | 262,144 (256K) | `us-central1` (US) | 127 tok/s | $0.30 / $0.90 | fp4 | function calling, reasoning, code |
| `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` | 30B A3B MoE | 262,144 (256K) | `eu-north1` (FI) | 60 tok/s | $0.06 / $0.24 | fp8 | function calling, reasoning, code |
| `nvidia/Nemotron-3_5-Lightning` | 30B A3B | 1,048,576 (1M) | `eu-north1` (FI) | 314.12 tok/s | $0.06 / $0.24 | bf16 | function calling, reasoning, code, MTP |

### 2.1 The heavy / fast split used by CodeLens

| Role | Default model ID | Why |
| --- | --- | --- |
| **Heavy** (`NEBIUS_MODEL_HEAVY`) | `nvidia/Nemotron-3-Ultra-550b-a55b` | Largest Nemotron available: 550B A55B MoE with a **1M-token context window**, so large repositories can be analysed in very few, very large calls instead of being aggressively truncated. Published at 523 tok/s and $1/$3 per 1M tokens — still faster than the 120B model's 127 tok/s. Used for security triage, architecture reasoning, hotspot explanations and Fix Advisor diffs. |
| **Fast** (`NEBIUS_MODEL_FAST`) | `nvidia/Nemotron-3_5-Lightning` | Cheapest tier ($0.06/$0.24, 60x cheaper than Ultra on input) with a **1M context window** and the **highest published throughput of the cheap models at 314 tok/s**, plus function calling. Used for chat, streaming answers, per-file triage and any high-volume call. |

Both are overridable per deployment, and `/api/models` reflects whatever is configured, so a
judge can point the app at any other catalog model without a code change.

### 2.2 Region caveat (must be validated with a real key)

The two chosen models are published in **different regions**: Ultra in `us-central1`
(United States) and Lightning in `eu-north1` (Finland). The default host
`api.tokenfactory.nebius.com` is documented as the main entry point, and per-region hosts
also exist (for example `https://api.tokenfactory.eu-west1.nebius.com/v1/`).

If the default host turns out to serve only one region, the fix is to set `NEBIUS_BASE_URL`
to the regional host (or run the two roles in different regions) — which is exactly why the
base URL is an environment variable rather than a constant. This is listed in Open Questions
and is verified in Phase 5 before anything is claimed.

---

## 3. Tavily Search API

Source: <https://docs.tavily.com/documentation/api-reference/endpoint/search>.

| Item | Value |
| --- | --- |
| Endpoint | `POST https://api.tavily.com/search` |
| Auth | `Authorization: Bearer tvly-...` (also accepted as an `api_key` JSON field) |
| Required param | `query` |

Selected parameters: `search_depth` (`advanced` \| `basic` \| `fast` \| `ultra-fast`, default
`basic`), `max_results` (0–20, default 10), `chunks_per_source` (1–3),
`include_answer` (`true` \| `basic` \| `advanced`), `include_raw_content`
(`true` \| `markdown` | `text`), `topic` (`general` | `news` | `finance`), `time_range`,
`start_date` / `end_date`, `include_domains`, `exclude_domains`,
`include_domains_mode` (`restrict` | `prefer`), `country`, `language`, `auto_parameters`,
`exact_match`, `include_usage`, `safe_search`.

Response shape:

```jsonc
{
  "query": "...",
  "answer": "...",
  "images": [ ... ],
  "results": [ { "title": "...", "url": "...", "content": "...", "score": 0.9, "raw_content": "...", "published_date": "...", "favicon": "...", "id": "..." } ],
  "response_time": 1.23,
  "usage": { ... },
  "request_id": "..."
}
```

HTTP error codes: `400` bad request, `401` unauthorized, `422` unprocessable, `429` rate
limited, `432`/`433` plan/quota limits, `500` server error.

CodeLens uses `search_depth: "advanced"` and `include_answer: true` for the calls that
ground a dependency or vulnerability verdict, and treats every Tavily result as
**a source to cite, not as a verdict**: the model must only claim what the returned
content supports. If `TAVILY_API_KEY` is absent the UI labels the finding
`unverified` instead of inventing a link.

---

## 4. Nebius AI Cloud — Serverless Endpoints (hosting option)

Source: [Serverless AI](https://docs.nebius.com/serverless/index.md),
[endpoints quickstart](https://docs.nebius.com/serverless/quickstart/endpoints.md),
[managing endpoints](https://docs.nebius.com/serverless/endpoints/manage.md),
[lifecycle](https://docs.nebius.com/serverless/lifecycle.md).

Serverless AI runs containerised workloads as **Devlabs**, **jobs** or **endpoints** with
per-second billing. It is available in every region except `eu-north2`, `eu-south1`, `us-north1`.

Prerequisites: the [Nebius CLI](https://docs.nebius.com/cli/install) with a project ID in
`~/.nebius/config.yaml`, and quotas for at least one VM and one VPC allocation.

Create an endpoint (CLI form, for the CodeLens backend image):

```bash
export AUTH_TOKEN=$(openssl rand -hex 32)

nebius ai endpoint create \
  --name codelens-api \
  --image <registry>/<repo>:<tag> \
  --platform cpu-d3 \
  --preset 4vcpu-16gb \
  --public \
  --container-port 8000 \
  --auth token \
  --token "$AUTH_TOKEN" \
  --subnet-id <subnet_ID>
```

* `--preset` must match the platform (`/compute/virtual-machines/types`).
* Every declared HTTP port is reachable through the endpoint's **managed HTTPS URL**;
  `--public` is not required for that.
* REST API equivalent: `POST https://api.nebius.cloud/ai/v1/endpoints` with
  `metadata.parentId` = project ID and a `spec` block.
* Read the public URL:
  `nebius ai endpoint get <endpoint_ID> --format json | jq -r '.status.public_endpoints[] | select(startswith("https://"))'`
  (REST field name: `status.publicEndpoints`).
* Secrets go in [SecretStash](https://docs.nebius.com/mysterybox/overview) and are injected as
  environment variables — this is how `NEBIUS_API_KEY` / `TAVILY_API_KEY` are supplied
  without ever appearing in an image or a commit.
* Lifecycle states for an endpoint:
  `PROVISIONING` -> `STARTING` -> `IMAGE_PULLING` -> `RUNNING` -> `STOPPING` -> `STOPPED`
  (-> `STARTING` ... to restart), and `ERROR` for failures (endpoints have no `FAILED` state);
  read `status.state_details.code` / `.message` for the cause. Commands:
  `nebius ai endpoint list | get | start | stop | delete`.
* Compute platforms: <https://docs.nebius.com/compute/virtual-machines/types>.

This is the deployment path described in [`DEPLOY_NEBIUS.md`](DEPLOY_NEBIUS.md).

---

## 5. Open questions — verified live in Phase 5, before any claim is made public

1. **Cross-region reachability.** Does `https://api.tokenfactory.nebius.com/v1/` serve both
   `us-central1` models (Nemotron-3-Ultra) and `eu-north1` models (Nemotron-3.5-Lightning)?
   If not, which regional base URL should `NEBIUS_BASE_URL` use?
2. **Model ID availability.** `GET /v1/models` is the authority for the account's IDs. Confirm
   the exact IDs from section 2 and confirm whether a `-fast` flavor ID exists for them.
3. **`-fast` flavor availability** for the Nemotron models actually used.
4. **JSON mode on Nemotron.** The structured-output docs note that only models tagged
   `JSON mode` guarantee it. Confirm actual behaviour for the heavy and fast models and keep
   the parse-and-repair fallback either way.
5. **Measured latency and token usage** for both roles, recorded in `docs/TEST_REPORT.md`.

Nothing in this file may be restated as a performance claim until it appears in
`docs/TEST_REPORT.md` with the real request behind it.
