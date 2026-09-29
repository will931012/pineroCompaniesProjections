"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { isApiError, setCsrfToken } from "./api/client";
import { api, type Role, type SessionOut } from "./api/endpoints";

export const SESSION_KEY = ["session"] as const;
const ROLE_RANK: Record<Role, number> = { viewer: 1, analyst: 2, admin: 3 };

export function hasRole(role: Role | undefined, minimum: Role): boolean {
  return role !== undefined && ROLE_RANK[role] >= ROLE_RANK[minimum];
}

/** Current session; redirects to /login when the API reports no valid session. */
export function useSession() {
  const router = useRouter();
  const query = useQuery({
    queryKey: SESSION_KEY,
    queryFn: async () => {
      const session = await api.session();
      setCsrfToken(session.csrf_token);
      return session;
    },
    retry: (count, error) => !isApiError(error, "authentication_required") && count < 2,
    staleTime: 60_000,
  });

  const unauthenticated = isApiError(query.error, "authentication_required");
  useEffect(() => {
    if (unauthenticated) {
      const next = `${window.location.pathname}${window.location.search}`;
      router.replace(next === "/" ? "/login" : `/login?next=${encodeURIComponent(next)}`);
    }
  }, [unauthenticated, router]);

  return query;
}

/** Store a freshly issued session so the workspace renders without another round trip. */
export function useAcceptSession() {
  const client = useQueryClient();
  return (session: SessionOut) => {
    setCsrfToken(session.csrf_token);
    client.setQueryData(SESSION_KEY, session);
  };
}

/** Only allow same-site relative redirects after sign-in. */
export function safeNextPath(next: string | null): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/";
  return next;
}
