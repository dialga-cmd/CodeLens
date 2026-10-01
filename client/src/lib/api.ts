/**
 * One place that knows how to talk to the CodeLens API.
 *
 * Two things every call needs: the base URL, and a bearer token that may or may
 * not exist. The backend runs in guest mode unless Firebase is configured, so
 * "no token" is a normal state and not an error - the request simply goes out
 * unauthenticated and the server decides what that means.
 */

export const API_BASE_URL = (
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000"
).replace(/\/$/, "");

/** Where a judge can confirm the backend is up without an account. */
export const HEALTH_URL = process.env.NEXT_PUBLIC_RENDER_BACKEND_HEALTH_URL || `${API_BASE_URL}/health`;

export class ApiError extends Error {
  readonly status: number;
  readonly retryAfter?: number;

  constructor(message: string, status: number, retryAfter?: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

type TokenGetter = () => Promise<string | null>;

/** Headers for a JSON request, with the token when there is one. */
export async function authHeaders(getToken?: TokenGetter): Promise<HeadersInit> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (getToken) {
    const token = await getToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

async function readError(response: Response): Promise<string> {
  try {
    const payload = await response.json();
    const detail = payload?.detail ?? payload?.error ?? payload?.message;
    if (typeof detail === "string" && detail) return detail;
    if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg);
  } catch {
    // A non-JSON body is more useful reported by status than parsed.
  }
  return `The server responded with ${response.status}.`;
}

/** Absolute paths are used as-is; everything else hangs off the API base URL. */
function resolve(path: string): string {
  return /^https?:\/\//i.test(path) ? path : `${API_BASE_URL}${path}`;
}

async function request<T>(
  path: string,
  init: RequestInit & { getToken?: TokenGetter } = {}
): Promise<T> {
  const { getToken, ...rest } = init;
  const response = await fetch(resolve(path), {
    ...rest,
    headers: { ...(await authHeaders(getToken)), ...(rest.headers || {}) },
  });

  if (!response.ok) {
    const retryAfter = Number(response.headers.get("Retry-After") || "") || undefined;
    throw new ApiError(await readError(response), response.status, retryAfter);
  }
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string, getToken?: TokenGetter) => request<T>(path, { method: "GET", getToken }),

  post: <T>(path: string, body: unknown, getToken?: TokenGetter) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body), getToken }),

  health: () => request<HealthReport>(HEALTH_URL),

  config: () => request<PublicConfig>("/api/config"),

  demoCatalogue: () => request<{ available: DemoEntry[] }>("/api/demo"),

  demo: (slug: string) => request<AnalysisResult>(`/api/demo/${slug}`),

  analysis: (repoId: string, getToken?: TokenGetter) =>
    request<AnalysisResult>(`/api/analysis/${encodeURIComponent(repoId)}`, { getToken }),

  repoFile: (repoId: string, filePath: string, getToken?: TokenGetter) =>
    request<RepoFile>(
      "/repo/file",
      { method: "POST", body: JSON.stringify({ repo_id: repoId, file_path: filePath }), getToken }
    ),

  proposeFix: (payload: FixRequest, getToken?: TokenGetter) =>
    request<FixResponse>("/api/fix", {
      method: "POST",
      body: JSON.stringify(payload),
      getToken,
    }),
};

/**
 * The URL for the analysis event stream.
 *
 * `EventSource` cannot set an Authorization header, so the token goes in the
 * query string - which is exactly why the backend also accepts `?token=`. The
 * token is omitted entirely in guest mode.
 */
export async function analysisStreamUrl(repoUrl: string, getToken?: TokenGetter): Promise<string> {
  const params = new URLSearchParams({ url: repoUrl });
  if (getToken) {
    const token = await getToken();
    if (token) params.set("token", token);
  }
  return `${API_BASE_URL}/analyze/stream?${params.toString()}`;
}

// --------------------------------------------------------------------------- //
// response shapes
// --------------------------------------------------------------------------- //

export interface HealthReport {
  status: string;
  version: string;
  llm: { configured: boolean; models: { heavy: string; fast: string } };
  research: { configured: boolean };
  auth: { mode: "firebase" | "guest"; required: boolean };
}

export interface PublicConfig {
  auth: { mode: "firebase" | "guest"; required: boolean; warnings: string[] };
  models: { heavy: string; fast: string; configured: boolean; provider: string };
  research: { provider: string; configured: boolean };
  rate_limits: { enabled: boolean; limits: Record<string, string> };
  capabilities: Record<string, boolean>;
}

export interface DemoEntry {
  slug: string;
  label: string;
  repo_url: string;
  snapshot: string;
  head_sha: string;
  generated_at: string;
  stats?: Record<string, number>;
}

/** A web page a verdict was checked against, as returned by Tavily research. */
export interface Source {
  title: string;
  url: string;
  snippet?: string;
}

/** A file an answer is based on, as returned by the chat service. */
export interface ChatSource {
  path: string;
  /** `context` when the analysis summary already carried it, `tool` when the model read it. */
  source: "context" | "tool";
}

export interface Finding {
  id: string;
  rule?: string;
  name: string;
  severity: string;
  description: string;
  file_path: string;
  line: number | string;
  snippet?: string;
  recommendation?: string;
  detector?: string;
  confidence?: string;
  triage?: string;
  severity_reason?: string;
  research?: { query?: string; answer?: string; sources?: Source[] };
}

export interface Hotspot {
  file_path: string;
  language: string;
  score: number;
  reasons: string[];
  complexity: number;
  fan_in: number;
  fan_out: number;
  loc: number;
  commits: number;
  finding_count: number;
  functions: Array<{ name: string; complexity: number; start_line: number; lines: number }>;
  signals?: Record<string, number>;
}

export interface AnalyzedFile {
  file_path: string;
  language: string;
  complexity: number;
  loc: number;
  functions: Array<{ name: string }>;
  classes: Array<{ name: string }>;
  fan_in: number;
  fan_out: number;
  finding_count: number;
  vulnerabilities?: Finding[];
  parsed_with?: string;
}

export interface Dependency {
  name: string;
  version: string;
  declared_spec?: string;
  ecosystem?: string;
  ecosystem_label?: string;
  severity?: string;
  cves?: string[];
  summary?: string;
  fixed_in?: string;
  latest_version?: string;
  behind_by?: string;
  confidence?: string;
  reason?: string;
  declared_in?: string;
  sources?: Source[];
}

export interface Architecture {
  summary?: string;
  pattern?: string;
  layers?: Array<{ name?: string; files?: string[]; responsibility?: string }>;
  entry_points?: Array<{ file_path?: string; role?: string; calls?: string[] }>;
  data_flow?: string[];
  extension_points?: Array<{ file_path?: string; how?: string }>;
  risks?: Array<{ file_path?: string; risk?: string }>;
  generated_by?: string;
}

export interface AnalysisResult {
  repo_id: string;
  repo_url: string;
  head_sha: string;
  files: AnalyzedFile[];
  graph: { nodes?: any[]; links?: any[] };
  hotspots: Hotspot[];
  vulnerabilities: Finding[];
  security_summary: Record<string, any>;
  dependency_manifests: Array<{ file_path: string; type: string; ecosystem: string; dependencies: Dependency[] }>;
  tech_stack: Record<string, any>;
  ai_dependencies: { vulnerable: Dependency[]; outdated: Dependency[] };
  dependency_research: Record<string, any>;
  architecture: Architecture;
  llm_usage: Array<Record<string, any>>;
  truncated?: boolean;
  cached?: boolean;
  stats: Record<string, any>;
  demo?: {
    slug: string;
    label: string;
    repo_url: string;
    head_sha?: string;
    generated_at?: string;
    live_required_for: string[];
  };
}

export interface RepoFile {
  file_path: string;
  content: string;
  language: string;
  size: number;
}

export interface FixRequest {
  repo_id: string;
  finding_id?: string;
  file_path?: string;
  line?: number;
}

export interface ProposedFix {
  finding_id: string;
  file_path: string;
  line: number;
  diff: string;
  explanation: string;
  risk: string;
  risk_notes: string[];
  alternatives: string[];
  applies: boolean;
  validation: string;
  attempts: number;
  model: string;
}

export interface FixResponse {
  finding?: Finding;
  fix?: ProposedFix;
  error?: string;
}