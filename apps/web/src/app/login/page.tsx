"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState, type FormEvent } from "react";
import { AuthCard } from "@/components/AuthCard";
import { api } from "@/lib/api/endpoints";
import { safeNextPath, useAcceptSession } from "@/lib/session";

const SSO_ERRORS: Record<string, string> = {
  sso_failed: "The identity provider could not complete sign-in.",
  sso_claims: "The identity provider did not return the required claims.",
  oidc_email_unverified: "An account with this email exists; the provider must verify the email first.",
  account_disabled: "This account is disabled.",
};

function LoginForm() {
  const params = useSearchParams();
  const router = useRouter();
  const accept = useAcceptSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const config = useQuery({ queryKey: ["auth-config"], queryFn: api.authConfig });
  const login = useMutation({
    mutationFn: () => api.login({ email, password }),
    onSuccess: (session) => {
      accept(session);
      router.replace(safeNextPath(params.get("next")));
    },
  });
  const ssoError = params.get("error");
  const authDisabled = config.data?.auth_disabled === true;

  // Sign-in is switched off on the API: go straight to the app, which signs in automatically.
  useEffect(() => {
    if (authDisabled) router.replace(safeNextPath(params.get("next")));
  }, [authDisabled, params, router]);

  function submit(event: FormEvent) {
    event.preventDefault();
    login.mutate();
  }

  return (
    <AuthCard
      title="Sign in"
      subtitle="Source-first company research."
      footer={config.data?.registration && (
        <>No account? <Link href="/register">Create one</Link></>
      )}
    >
      {ssoError && <p className="form-error" role="alert">{SSO_ERRORS[ssoError] ?? "Sign-in failed."}</p>}
      {config.isError && (
        <p className="form-error" role="alert">The research API is unreachable. Is the API service running?</p>
      )}
      {config.data?.password_login !== false && (
        <form className="auth-form" onSubmit={submit}>
          <label>Email
            <input autoComplete="email" autoFocus onChange={(e) => setEmail(e.target.value)} required
              type="email" value={email} />
          </label>
          <label>Password
            <input autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} required
              type="password" value={password} />
          </label>
          {login.isError && <p className="form-error" role="alert">{login.error.message}</p>}
          <button className="button-primary" disabled={login.isPending} type="submit">
            {login.isPending ? "Signing in…" : "Sign in"}
          </button>
        </form>
      )}
      {config.data?.oidc && (
        // A native full-page GET: the API answers with a redirect to the identity provider.
        <form action="/api/v1/auth/oidc/login" method="get">
          <button className="button-secondary sso-button" type="submit">Continue with single sign-on</button>
        </form>
      )}
    </AuthCard>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
