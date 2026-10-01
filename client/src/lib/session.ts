/**
 * Where the client keeps "which analysis am I looking at".
 *
 * The full result is large, so it is not kept here: it stays in session storage
 * for the page that produced it, and the repository id is kept here so a reload,
 * a refresh or a shared link can ask the backend to hand the same analysis back.
 * That is what makes a results URL survive a browser restart.
 */

const REPO_ID_KEY = "codelens.repoId";
const REPO_URL_KEY = "codelens.repoUrl";
const RESULTS_KEY = "analysisResults";

export function rememberAnalysis(repoId: string, repoUrl: string): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(REPO_ID_KEY, repoId);
  window.sessionStorage.setItem(REPO_URL_KEY, repoUrl);
}

export function lastAnalysis(): { repoId: string; repoUrl: string } {
  if (typeof window === "undefined") return { repoId: "", repoUrl: "" };
  return {
    repoId: window.sessionStorage.getItem(REPO_ID_KEY) || "",
    repoUrl: window.sessionStorage.getItem(REPO_URL_KEY) || "",
  };
}

export function stashResults(results: unknown): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.setItem(RESULTS_KEY, JSON.stringify(results));
}

export function readStashedResults<T>(): T | null {
  if (typeof window === "undefined") return null;
  const raw = window.sessionStorage.getItem(RESULTS_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

export function clearAnalysis(): void {
  if (typeof window === "undefined") return;
  window.sessionStorage.removeItem(REPO_ID_KEY);
  window.sessionStorage.removeItem(REPO_URL_KEY);
  window.sessionStorage.removeItem(RESULTS_KEY);
}