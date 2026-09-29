"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { CompanySearch } from "@/components/CompanySearch";
import { PageHeading, Panel } from "@/components/ui";

function Directory() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const [query, setQuery] = useState(params.get("q") ?? "");

  function update(value: string) {
    setQuery(value);
    const next = new URLSearchParams(params);
    if (value.trim()) next.set("q", value);
    else next.delete("q");
    router.replace(next.size ? `${pathname}?${next}` : pathname, { scroll: false });
  }

  return (
    <Panel kicker="SEC EDGAR · OFFICIAL TICKER FILE" title="Company directory">
      <CompanySearch query={query} onQueryChange={update} limit={25} autoFocus />
      {!query.trim() && (
        <p className="directory-note">
          Search by ticker (exact and prefix matches rank first) or by any part of the registrant
          name. Classification uses SEC SIC codes; GICS sectors are licensed and not used.
        </p>
      )}
    </Panel>
  );
}

export default function CompaniesPage() {
  return (
    <>
      <PageHeading kicker="COMPANY INTELLIGENCE" title="Companies"
        subtitle="Every U.S.-listed SEC registrant with a ticker, with inactive listings kept for history." />
      <Suspense>
        <Directory />
      </Suspense>
    </>
  );
}
