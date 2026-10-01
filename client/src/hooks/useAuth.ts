"use client";

import { useCallback, useEffect, useState } from "react";
import { auth } from "@/lib/firebase";
import {
  GoogleAuthProvider,
  onAuthStateChanged,
  signInWithPopup,
  signOut,
  type User,
} from "firebase/auth";

/**
 * Who the browser is, as far as the API is concerned.
 *
 * Sign-in is optional. The backend decides whether a request needs a token, and
 * this hook is what lets the client answer the same question: when Firebase is
 * not configured - the normal case for the demo - there is no user and no token,
 * and every call goes out unauthenticated into guest mode. `isGuest` is what the
 * UI uses to say so honestly rather than implying an account exists.
 */
export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!auth) {
      setLoading(false);
      return;
    }
    const unsubscribe = onAuthStateChanged(auth, (next) => {
      setUser(next);
      setLoading(false);
    });
    return () => unsubscribe();
  }, []);

  /** The bearer token, or null when nobody is signed in. */
  const getIdToken = useCallback(async (): Promise<string | null> => {
    if (!user) return null;
    try {
      return await user.getIdToken();
    } catch {
      // A refresh failure must not break the request path; the server will
      // answer 401 and the UI can offer sign-in again.
      return null;
    }
  }, [user]);

  const loginWithGoogle = useCallback(async () => {
    if (!auth) throw new Error("Firebase is not configured on this deployment.");
    return signInWithPopup(auth, new GoogleAuthProvider());
  }, []);

  const logout = useCallback(async () => {
    if (!auth) return;
    await signOut(auth);
  }, []);

  return {
    user,
    loading,
    getIdToken,
    loginWithGoogle,
    logout,
    isGuest: !loading && !user,
    /** Firebase is not configured here, so signing in is not offered at all. */
    canSignIn: Boolean(auth),
  };
}