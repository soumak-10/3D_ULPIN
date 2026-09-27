"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Building2, MapPin, Plus } from "lucide-react";
import * as React from "react";

import {
  EmptyState,
  ErrorState,
  PageHeader,
  Pagination,
  UlpinChip,
} from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SkeletonCards } from "@/components/ui/skeleton";
import { buildings } from "@/lib/api/endpoints";
import { STATE_CODES } from "@/lib/constants";
import { formatNumber } from "@/lib/utils";
import { useAuth } from "@/providers/auth-provider";

const ANY = "__any";

/** The building register (Request E). */
export default function BuildingsPage() {
  const router = useRouter();
  const sp = useSearchParams();
  const { isStaff } = useAuth();

  const page = Number(sp.get("page") ?? 1) || 1;
  const pageSize = Number(sp.get("page_size") ?? 20) || 20;
  const q = sp.get("q") ?? "";
  const state = sp.get("state_code") ?? undefined;

  const [draft, setDraft] = React.useState(q);
  React.useEffect(() => setDraft(q), [q]);

  const push = (next: Record<string, string | number | undefined>) => {
    const params = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(next)) {
      if (v === undefined || v === "") params.delete(k);
      else params.set(k, String(v));
    }
    if (!("page" in next)) params.delete("page");
    router.push(`/buildings?${params.toString()}`, { scroll: false });
  };

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ["buildings", { page, pageSize, q, state }],
    queryFn: () =>
      buildings.list({ page, page_size: pageSize, q: q || undefined, state_code: state }),
    placeholderData: (prev) => prev,
  });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Buildings"
        description="Every registered structure, with its floors and units."
      >
        {isStaff ? (
          <Button asChild>
            <Link href="/buildings/new">
              <Plus /> Register building
            </Link>
          </Button>
        ) : null}
      </PageHeader>

      <div className="flex flex-col gap-2 sm:flex-row">
        <form
          className="flex-1"
          onSubmit={(e) => {
            e.preventDefault();
            push({ q: draft.trim() || undefined });
          }}
        >
          <Input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Building name, locality or city"
            aria-label="Filter buildings"
          />
        </form>
        <Select
          value={state ?? ANY}
          onValueChange={(v) => push({ state_code: v === ANY ? undefined : v })}
        >
          <SelectTrigger className="sm:w-48" aria-label="State">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>All states</SelectItem>
            {STATE_CODES.map((s) => (
              <SelectItem key={s.value} value={s.value}>
                {s.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isLoading ? (
        <SkeletonCards count={4} className="h-24" />
      ) : (data?.items.length ?? 0) === 0 ? (
        <EmptyState
          icon={Building2}
          title={q || state ? "No building matches" : "No buildings yet"}
          description={
            q || state
              ? "Clear the filters, or search the whole register instead."
              : "Register a building to start issuing identifiers for the units inside it."
          }
        />
      ) : (
        <div className="space-y-4">
          <div className={isFetching ? "space-y-3 opacity-60" : "space-y-3"}>
            {data!.items.map((b) => (
              <Card key={b.building_id} className="transition-colors hover:border-primary/40">
                <CardContent className="flex flex-wrap items-start justify-between gap-4 p-4">
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Link
                        href={`/buildings/${b.building_id}`}
                        className="text-base font-semibold hover:underline"
                      >
                        {b.building_name}
                      </Link>
                      {b.short_code ? <UlpinChip code={b.short_code} /> : null}
                      <Badge variant="outline">{b.status.toLowerCase()}</Badge>
                    </div>
                    <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
                      <MapPin className="size-3.5 shrink-0" aria-hidden />
                      {[b.address_line1, b.locality, b.city, b.state_code, b.pincode]
                        .filter(Boolean)
                        .join(", ")}
                    </p>
                    <p className="text-xs tabular-nums text-muted-foreground">
                      {b.total_floors} floors
                      {b.basement_floors > 0 ? ` (+${b.basement_floors} basement)` : ""}
                      {b.unit_count !== undefined ? ` · ${formatNumber(b.unit_count)} units` : ""}
                      {b.year_built ? ` · built ${b.year_built}` : ""}
                      {" · "}
                      {b.latitude.toFixed(5)}, {b.longitude.toFixed(5)}
                    </p>
                  </div>
                  <div className="flex gap-2">
                    <Button variant="outline" size="sm" asChild>
                      <Link href={`/viewer?building=${b.building_id}`}>View in 3D</Link>
                    </Button>
                    <Button variant="ghost" size="sm" asChild>
                      <Link href={`/buildings/${b.building_id}`}>Open</Link>
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>

          <Pagination
            page={data!.page}
            pageSize={data!.page_size}
            total={data!.total}
            pages={totalPages}
            onPageChange={(p) => push({ page: p })}
            onPageSizeChange={(s) => push({ page_size: s, page: undefined })}
          />
        </div>
      )}
    </div>
  );
}
