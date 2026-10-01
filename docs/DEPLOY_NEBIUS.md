# Deploying CodeLens

Two pieces: a FastAPI service that clones and analyses, and a static Next.js
export that renders it. They can be hosted anywhere. This document covers
Nebius AI Cloud for the API, because that is one of the two ways to satisfy the
hackathon's "use the platform" requirement, and the static host for the client.

The API is stateless apart from a data directory, so any Docker host works.
Nothing in the image is region-specific and no port besides 8000 is needed.

---

## 0. Before you start

You need three things. Two are free.

| What | Where | Needed for |
| --- | --- | --- |
| A Nebius AI Cloud project | [console.nebius.com](https://console.nebius.com/) | the API host |
| A Nebius Token Factory key | [tokenfactory.nebius.com](https://tokenfactory.nebius.com/) | the models |
| A GitHub container registry + token | github.com/settings/tokens | pushing the image |

A Tavily key from [app.tavily.com](https://app.tavily.com/home) is optional.
Without it the analysis still runs; version and CVE verdicts are labelled
`unverified` instead of being asserted. This is deliberate — see
[Limitations](DEPLOY_NEBIUS.md#what-is-degraded-without-tavily).

---

## 1. The API image

`server/Dockerfile` is a two-stage build. The compiler toolchain exists only to
build the two native tree-sitter packages and is not in the shipped image, which
installs just `git` and `ca-certificates` at runtime and runs as uid 10001
rather than root. `git` is not optional: analysis clones the repository it is
asked about.

Build and push from the repository root:

```bash
cd server
docker build -t ghcr.io/YOUR_GITHUB_USER/codelens-api:1.0.0 .
docker push ghcr.io/YOUR_GITHUB_USER/codelens-api:1.0.0
```

The image listens on `$PORT` (default 8000) and has a `HEALTHCHECK` against
`/health`, so a platform can wait for it without guessing.

### Test the image locally first

```bash
docker run --rm -p 8000:8000 \
  -e NEBIUS_API_KEY="$NEBIUS_API_KEY" \
  -e TAVILY_API_KEY="$TAVILY_API_KEY" \
  -e CODELENS_ALLOWED_ORIGINS="http://localhost:3000" \
  ghcr.io/YOUR_GITHUB_USER/codelens-api:1.0.0

curl -s localhost:8000/health | python3 -m json.tool
```

`"llm": {"configured": true}` is the line that matters. If it is `false`, the
models are not configured and every model-backed feature will report itself
unavailable rather than pretending.

---

## 2. Run it on Nebius AI Cloud

### 2a. The CLI way

Install the [Nebius CLI](https://docs.nebius.com/cli/install) and make sure a
project id is in `~/.nebius/config.yaml`.

```bash
export AUTH_TOKEN=$(openssl rand -hex 32)

nebius ai endpoint create \
  --name codelens-api \
  --image ghcr.io/YOUR_GITHUB_USER/codelens-api:1.0.0 \
  --platform cpu-d3 \
  --preset 4vcpu-16gb \
  --public \
  --container-port 8000 \
  --auth token \
  --token "$AUTH_TOKEN" \
  --subnet-id SUBNET_ID
```

`--preset` has to match the platform; the catalogue is at
[/compute/virtual-machines/types](https://docs.nebius.com/compute/virtual-machines/types).
Drop `--public` if you would rather reach the endpoint through the endpoint's
managed HTTPS URL only — that URL is always available for a declared HTTP port.

Read the URL back:

```bash
nebius ai endpoint get ENDPOINT_ID --format json \
  | jq -r '.status.public_endpoints[] | select(startswith("https://"))'
```

Lifecycle is `PROVISIONING → STARTING → IMAGE_PULLING → RUNNING`, and `ERROR`
if something failed (endpoints have no `FAILED` state, so read
`status.state_details.code` and `.message` for the reason). `nebius ai endpoint
start|stop|list|delete` manage it after that.

### 2b. The REST way

```bash
curl -X POST "https://api.nebius.cloud/ai/v1/endpoints?parentId=PROJECT_ID" \
  -H "Authorization: Bearer $NEBIUS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "metadata": {"name": "codelens-api"},
    "spec": {
      "template": {
        "image": "ghcr.io/YOUR_GITHUB_USER/codelens-api:1.0.0",
        "resources": {"cpu": 4, "memory_gib": 16},
        "containerPort": 8000
      },
      "platform": "cpu-d3",
      "subnetId": "SUBNET_ID"
    }
  }'
```

### 2c. Secrets

Put `NEBIUS_API_KEY` and `TAVILY_API_KEY` in
[SecretStash](https://docs.nebius.com/mysterybox/overview) and reference them
from the endpoint. They are injected as environment variables at start, so
neither appears in an image, a commit or a build log.

If your image pulls from a private registry, add a registry secret to the
SecretStash too.

### 2d. Storage

Analyses and clones are written to `CODELENS_DATA_DIR`, which the image sets to
`/data`. Without a volume, analyses live in the container filesystem and
disappear when the endpoint is replaced — survivable, but the cache and the
chat tools go with it. Attach a volume at `/data` for a deployment that will be
used more than once.

---

## 3. The client

The client is a static export (`output: "export"` in `next.config.mjs`), so it is
files rather than a service. Any static host serves it; the two requirements are
that `/results` and `/dashboard` resolve to their own `index.html` and that
unknown paths fall back to `/index.html`.

```bash
cd client
cp .env.example .env.local     # then set NEXT_PUBLIC_API_BASE_URL
npm ci
npm run build                  # writes client/out/
```

Because this is a static export, `NEXT_PUBLIC_*` values are inlined into the
bundle at build time. Changing one means rebuilding, not restarting. Nothing
secret belongs in a `NEXT_PUBLIC_` variable — those values are public by
definition.

### Firebase Hosting

`firebase.json` in the repository root is already configured for `client/out`.
Two rewrites are needed for a static export; with the catch-all alone, a reload
of `/results` serves the landing page:

```json
{
  "hosting": {
    "public": "client/out",
    "ignore": ["firebase.json", "**/.*", "**/node_modules/**"],
    "rewrites": [
      { "source": "/dashboard", "destination": "/dashboard.html" },
      { "source": "/results", "destination": "/results.html" },
      { "source": "**", "destination": "/index.html" }
    ]
  }
}
```

Then `npx firebase-tools login && npx firebase-tools deploy --only hosting`.

### CORS

The API reads `CODELENS_ALLOWED_ORIGINS`, comma separated. Set it to the exact
origin the client is served from — `https://your-app.web.app` — not `*`, because
this API is public and unauthenticated by default. `ALLOW_VERCEL_ORIGINS=true`
also allows `*.vercel.app` preview deployments.

```
CODELENS_ALLOWED_ORIGINS=https://your-app.web.app
```

Confirm it before announcing the deployment:

```bash
curl -si -H "Origin: https://your-app.web.app" \
  "$API/health" | grep -i access-control-allow-origin
```

---

## 4. Verify the deployment

Work down this list. Each one is a real request, and each is a claim the README
makes.

```bash
API=https://your-endpoint.example.com

# 1. The service is up and reports what it can do.
curl -s $API/health | python3 -m json.tool
#    want: "status": "ok", llm.configured = true, research.configured = true

# 2. The models this instance will actually call.
curl -s $API/api/models | python3 -m json.tool

# 3. The stored demo analyses load, with no clone and no model call.
curl -s $API/api/demo | python3 -m json.tool
curl -s $API/api/demo/flask | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(len(d['files']),'files', len(d['hotspots']),'hotspots', len(d['vulnerabilities']),'findings')"

# 4. A real analysis, end to end, on a small repository. This endpoint is a
#    GET with query parameters, and the progress is Server-Sent Events.
curl -N "$API/analyze/stream?url=https://github.com/pallets/itsdangerous" | tail -5

# 5. Chat, against that analysis, streaming. REPO_ID comes from the analysis
#    response; the demo snapshots also carry one you can read.
curl -N -X POST $API/chat/stream \
  -H 'Content-Type: application/json' \
  -d '{"repo_id":"REPO_ID_FROM_STEP_4","query":"What is the most central module here, and why?"}' | tail -5

# 6. A fix that is checked against the clone before you see it.
curl -s -X POST $API/api/fix -H 'Content-Type: application/json' \
  -d '{"repo_id":"REPO_ID_FROM_STEP_4","finding_id":"FINDING_ID"}' | python3 -m json.tool
#    want: "applies": true
```

`repo_id` is in the analysis response and in every stored snapshot, so step 6
works against a demo analysis too — except that a stored demo has no clone on
disk, and the Fix Advisor needs one. The response says which.

Record the output of each in `docs/TEST_REPORT.md` before making any claim
about latency, cost or model quality. Anything not in that file is not a
result, it is an intention.

---

## 5. Environment variables

Everything has a working default except the API key. Values marked *tuning* are
worth knowing about if the deployment is shared.

### Server

| Variable | Default | Meaning |
| --- | --- | --- |
| `NEBIUS_API_KEY` | — | **required** for model features |
| `NEBIUS_BASE_URL` | `https://api.tokenfactory.nebius.com/v1/` | set to a regional host if your account is region-scoped |
| `NEBIUS_MODEL_HEAVY` | `nvidia/Nemotron-3-Ultra-550b-a55b` | reasoning-heavy work |
| `NEBIUS_MODEL_FAST` | `nvidia/Nemotron-3_5-Lightning` | chat, tools, per-file triage |
| `NEBIUS_MAX_RETRIES` | `3` | *tuning* — retries with backoff that honours `Retry-After` |
| `NEBIUS_MAX_CONCURRENCY` | `4` | *tuning* — client-side limiter, protects the rate-limit budget |
| `NEBIUS_REQUEST_TIMEOUT` | `120` | *tuning* — seconds |
| `TAVILY_API_KEY` | — | optional; grounds CVE and version claims |
| `CODELENS_TAVILY_ADVISORY_QUERIES` | `8` | *tuning* — Tavily credits per run |
| `CODELENS_DATA_DIR` | `/data` in the image | where clones and snapshots live |
| `CODELENS_SNAPSHOT_TTL_HOURS` | `24` | *tuning* — expiry, clone included |
| `CODELENS_MAX_SNAPSHOTS` | `40` | *tuning* — oldest are dropped past this |
| `CODELENS_MAX_REPO_MB` | `300` | repositories over this are refused and removed |
| `CODELENS_ALLOWED_ORIGINS` | — | comma-separated origins; required in production |
| `CODELENS_RATE_LIMIT` | `true` | per-client limits; a 429 carries `Retry-After` |
| `CODELENS_REQUIRE_AUTH` | `false` | set true only with Firebase configured |
| `PORT` | `8000` | container port |

### Client

| Variable | Default | Meaning |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | where the API is; inlined at build time |
| `NEXT_PUBLIC_FIREBASE_*` | unset | optional; only adds per-account history |

`server/.env.example` and `client/.env.example` list all of them with comments.

---

## What is degraded without Tavily

Worth stating plainly, because it is visible in the UI:

* dependency inventory is still parsed from every manifest, so the table is
  complete;
* each row is marked `unverified` with no sources, rather than given a
  plausible-looking CVE and a version someone remembered;
* the model still triages security findings, but the claim rests on the code
  and the 18 deterministic rules, not on a live advisory lookup.

Without `NEBIUS_API_KEY` the degradation is larger and the landing page says so
before you look at a report: static analysis, the graph, hotspots and the
dependency inventory all work; the review, the chat and the Fix Advisor report
themselves unavailable.

## What is not deployed here

`firebase.json` and `.firebaserc` are kept because the client is a static
export and the hosting config is three lines. The API in this repository is
containerised for Nebius AI Cloud; it also runs unchanged on any Docker host,
which is the honest way to describe it — nothing in the image or the
configuration depends on Nebius being the host. The models are called through
Token Factory, which is the part the hackathon asks for.
