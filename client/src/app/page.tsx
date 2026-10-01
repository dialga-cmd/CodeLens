"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import LandingPage from "@/components/LandingPage";
import { api, type HealthReport } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";

/**
 * The landing page: what CodeLens is, and the shortest possible path into it.
 *
 * Two ways forward are offered side by side because a judge has neither time
 * nor an account: analyse any public repository, or open a stored analysis that
 * the pipeline produced earlier. Neither requires signing in.
 */
export default function Home() {
  const router = useRouter();
  const { getIdToken, canSignIn, loginWithGoogle, loading } = useAuth();
  const [health, setHealth] = useState<HealthReport | null>(null);
  const [healthError, setHealthError] = useState("");

  // Asking the backend what it can do is the only honest way to label the demo.
  useEffect(() => {
    let cancelled = false;
    api
      .health()
      .then((report) => {
        if (!cancelled) setHealth(report);
      })
      .catch(() => {
        if (!cancelled) setHealthError("The CodeLens API is not reachable from this browser.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // The dashboard decides for itself whether it needs a token; passing one along
  // keeps the token in exactly one place in the client.
  const handleGetStarted = useCallback(async () => {
    await getIdToken();
    router.push("/dashboard");
  }, [getIdToken, router]);

  const handleSignIn = useCallback(async () => {
    await loginWithGoogle().catch(() => {
      // A closed popup is not worth an error screen; guest mode still works.
    });
  }, [loginWithGoogle]);

  return (
    <LandingPage
      onGetStarted={handleGetStarted}
      health={health}
      healthError={healthError}
      canSignIn={canSignIn && !loading}
      onSignIn={handleSignIn}
    />
  );
}