"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { AuthCard } from "@/components/AuthCard";
import { api } from "@/lib/api/endpoints";
import { useAcceptSession } from "@/lib/session";

const MIN_PASSWORD = 12;

export default function RegisterPage() {
  const router = useRouter();
  const accept = useAcceptSession();
  const [form, setForm] = useState({ display_name: "", email: "", password: "" });
  const config = useQuery({ queryKey: ["auth-config"], queryFn: api.authConfig });
  const register = useMutation({
    mutationFn: () => api.register(form),
    onSuccess: (session) => {
      accept(session);
      router.replace("/");
    },
  });
  const tooShort = form.password.length > 0 && form.password.length < MIN_PASSWORD;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!tooShort) register.mutate();
  }

  if (config.data && !config.data.registration) {
    return (
      <AuthCard title="Registration closed" subtitle="Ask an administrator for an account."
        footer={<Link href="/login">Back to sign in</Link>}>
        <p className="auth-copy">Self-service registration is disabled on this deployment.</p>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Create account" subtitle="New accounts get a personal research workspace."
      footer={<>Already registered? <Link href="/login">Sign in</Link></>}>
      <form className="auth-form" onSubmit={submit}>
        <label>Name
          <input autoComplete="name" maxLength={120} onChange={(e) => setForm({ ...form, display_name: e.target.value })}
            required value={form.display_name} />
        </label>
        <label>Email
          <input autoComplete="email" onChange={(e) => setForm({ ...form, email: e.target.value })} required
            type="email" value={form.email} />
        </label>
        <label>Password
          <input aria-describedby="password-help" autoComplete="new-password" minLength={MIN_PASSWORD}
            onChange={(e) => setForm({ ...form, password: e.target.value })} required type="password"
            value={form.password} />
          <small id="password-help" className={tooShort ? "form-error" : undefined}>
            At least {MIN_PASSWORD} characters. A passphrase works well.
          </small>
        </label>
        {register.isError && <p className="form-error" role="alert">{register.error.message}</p>}
        <button className="button-primary" disabled={register.isPending || tooShort} type="submit">
          {register.isPending ? "Creating account…" : "Create account"}
        </button>
      </form>
    </AuthCard>
  );
}
