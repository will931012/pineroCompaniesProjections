"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useState, type FormEvent } from "react";
import { api, type Watchlist } from "@/lib/api/endpoints";
import { hasRole, useSession } from "@/lib/session";
import { ErrorNotice, InlineState, Panel } from "./ui";

export const WATCHLISTS_KEY = ["watchlists"] as const;

export function WatchlistPanel() {
  const { data: session } = useSession();
  const canEdit = hasRole(session?.user.role, "analyst");
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const watchlists = useQuery({ queryKey: WATCHLISTS_KEY, queryFn: api.watchlists });

  const refresh = () => queryClient.invalidateQueries({ queryKey: WATCHLISTS_KEY });
  const create = useMutation({
    mutationFn: (value: string) => api.createWatchlist(value),
    onSuccess: () => {
      setName("");
      return refresh();
    },
  });
  const remove = useMutation({ mutationFn: api.deleteWatchlist, onSuccess: refresh });
  const removeItem = useMutation({
    mutationFn: ({ id, ticker }: { id: string; ticker: string }) => api.removeFromWatchlist(id, ticker),
    onSuccess: refresh,
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (name.trim()) create.mutate(name.trim());
  }

  return (
    <Panel kicker="PERSONAL WORKSPACE" title="Watchlists" className="watchlist-panel">
      {watchlists.isPending && <InlineState>Loading watchlists…</InlineState>}
      {watchlists.isError && <ErrorNotice error={watchlists.error} />}
      {watchlists.data?.length === 0 && (
        <InlineState>
          {canEdit ? "No watchlists yet. Create one, then add companies from their profile page."
            : "Your role is read-only, so watchlists cannot be created."}
        </InlineState>
      )}
      {watchlists.data?.map((watchlist: Watchlist) => (
        <section className="watchlist" key={watchlist.id}>
          <header>
            <strong>{watchlist.name}</strong>
            <small>{watchlist.items.length} {watchlist.items.length === 1 ? "company" : "companies"}</small>
            {canEdit && (
              <button className="icon-button" type="button" aria-label={`Delete ${watchlist.name}`}
                onClick={() => {
                  if (window.confirm(`Delete watchlist “${watchlist.name}”?`)) remove.mutate(watchlist.id);
                }}>
                <Trash2 size={14} />
              </button>
            )}
          </header>
          {watchlist.items.length > 0 && (
            <ul>
              {watchlist.items.map((item) => (
                <li key={item.ticker}>
                  <Link href={`/companies/${encodeURIComponent(item.ticker)}`}>
                    <b>{item.ticker}</b>
                    <span>{item.name}</span>
                    {!item.is_active && <em>inactive listing</em>}
                  </Link>
                  {canEdit && (
                    <button className="icon-button" type="button" aria-label={`Remove ${item.ticker}`}
                      onClick={() => removeItem.mutate({ id: watchlist.id, ticker: item.ticker })}>
                      <X size={13} />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      ))}
      {canEdit && (
        <form className="inline-form" onSubmit={submit}>
          <input aria-label="New watchlist name" maxLength={80} onChange={(e) => setName(e.target.value)}
            placeholder="New watchlist name" value={name} />
          <button className="button-secondary" disabled={!name.trim() || create.isPending} type="submit">
            <Plus size={14} /> Create
          </button>
        </form>
      )}
      {(create.isError || remove.isError || removeItem.isError) && (
        <ErrorNotice error={create.error ?? remove.error ?? removeItem.error} />
      )}
    </Panel>
  );
}
