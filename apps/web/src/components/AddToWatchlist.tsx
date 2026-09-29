"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Star } from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api/endpoints";
import { hasRole, useSession } from "@/lib/session";
import { WATCHLISTS_KEY } from "./WatchlistPanel";

export function AddToWatchlist({ ticker }: { ticker: string }) {
  const { data: session } = useSession();
  const queryClient = useQueryClient();
  const watchlists = useQuery({ queryKey: WATCHLISTS_KEY, queryFn: api.watchlists });
  const [choice, setChoice] = useState("");
  const add = useMutation({
    mutationFn: async () => {
      let id = choice || watchlists.data?.[0]?.id;
      if (!id) id = (await api.createWatchlist("Watchlist")).id;
      return api.addToWatchlist(id, ticker);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: WATCHLISTS_KEY }),
  });

  if (!hasRole(session?.user.role, "analyst")) return null;
  const containing = watchlists.data?.filter((w) => w.items.some((i) => i.ticker === ticker)) ?? [];

  return (
    <div className="add-watchlist">
      {(watchlists.data?.length ?? 0) > 1 && (
        <select aria-label="Watchlist" onChange={(e) => setChoice(e.target.value)}
          value={choice || watchlists.data?.[0]?.id}>
          {watchlists.data?.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
        </select>
      )}
      <button className="button-secondary" disabled={add.isPending} onClick={() => add.mutate()} type="button">
        {containing.length > 0 ? <Check size={14} /> : <Star size={14} />}
        {containing.length > 0 ? `In ${containing.map((w) => w.name).join(", ")}` : "Add to watchlist"}
      </button>
      {add.isError && <small className="form-error">{add.error.message}</small>}
    </div>
  );
}
