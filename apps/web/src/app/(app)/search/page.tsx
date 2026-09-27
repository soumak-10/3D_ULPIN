"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Building2, Filter, MapPin, Search as SearchIcon, X } from "lucide-react";
import * as React from "react";

import {
  EmptyState,
  ErrorState,
  Pagination,
  PageHeader,
  StatusBadge,
  UlpinChip,
} from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SkeletonCards } from "@/components/ui/skeleton";
import { search, type SearchParams } from "@/lib/api/endpoints";
import {
  OCCUPANCY_STATUSES,
  PROPERTY_TYPES,
  SEARCH_MODES,
  STATE_CODES,
  VERIFICATION_OUTCOMES,
} from "@/lib/constants";
import { cn, formatDate, formatNumber } from "@/lib/utils";
import type { SearchMode, SearchResult } from "@/types/api";

/**
 * Property search (Request H).
 *
 * All state lives in the URL. A search that cannot be pasted into a colleague's
 * message is not much use in an office where the answer to "which flat?" gets
 * forwarded three times, so every filter, the page and the mode are query
 * parameters and the back button steps through searches.
 */

const ANY = "__any";

/** Fields the rail can set, all optional, all mirrored in the URL. */
type Filters = Pick<
  SearchParams,
  | "unit_type"
  | "occupancy_status"
  | "verification_outcome"
  | "city"
  | "state"
  | "floor_number_min"
  | "floor_number_max"
  | "has_open_alerts"
>;

function readFilters(sp: URLSearchParams): Filters {
  const num = (key: string) => {
    const raw = sp.get(key);
    if (!raw) return undefined;
    const n = Number(raw);
    return Number.isFinite(n) ? n : undefined;
  };
  return {
    unit_type: sp.get("unit_type") ?? undefined,
    occupancy_status: sp.get("occupancy_status") ?? undefined,
    verification_outcome: sp.get("verification_outcome") ?? undefined,
    city: sp.get("city") ?? undefined,
    state: sp.get("state") ?? undefined,
    floor_number_min: num("floor_number_min"),
    floor_number_max: num("floor_number_max"),
    has_open_alerts: sp.get("has_open_alerts") === "1" ? true : undefined,
  };
}

function activeFilterCount(f: Filters): number {
  return Object.values(f).filter((v) => v !== undefined && v !== "").length;
}

function ResultCard({ row }: { row: SearchResult }) {
  const location = [row.address, row.city, row.state].filter(Boolean).join(", ");

  return (
    <Card className="transition-colors hover:border-primary/40">
      <CardContent className="space-y-3 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <Link
                href={`/units/${row.unit_id}`}
                className="text-base font-semibold hover:underline"
              >
                Unit {row.unit_number}
              </Link>
              {row.floor_number !== null ? (
                <Badge variant="outline">
                  {row.floor_label ?? `Floor ${row.floor_number}`}
                </Badge>
              ) : null}
              {row.unit_type ? (
                <Badge variant="neutral">{row.unit_type.replace(/_/g, " ").toLowerCase()}</Badge>
              ) : null}
            </div>
            {row.building_name ? (
              <p className="flex items-center gap-1.5 truncate text-sm text-muted-foreground">
                <Building2 className="size-3.5 shrink-0" aria-hidden />
                {row.building_id ? (
                  <Link href={`/buildings/${row.building_id}`} className="hover:underline">
                    {row.building_name}
                  </Link>
                ) : (
                  row.building_name
                )}
              </p>
            ) : null}
            {location ? (
              <p className="flex items-center gap-1.5 truncate text-xs text-muted-foreground">
                <MapPin className="size-3.5 shrink-0" aria-hidden />
                {location}
              </p>
            ) : null}
          </div>

          <div className="flex flex-col items-end gap-2">
            <StatusBadge outcome={row.verification_outcome} openAlerts={row.open_alert_count} />
            {row.occupancy_status ? (
              <Badge variant="outline">{row.occupancy_status.replace(/_/g, " ").toLowerCase()}</Badge>
            ) : null}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-t pt-3 text-xs">
          <UlpinChip code={row.short_code ?? row.ulpin_code} />
          <span className="text-muted-foreground">
            <span className="font-medium text-foreground">Owner: </span>
            {row.owner_names.length > 0 ? row.owner_names.join(", ") : "not recorded"}
          </span>
          <span className="text-muted-foreground">
            <span className="font-medium text-foreground">Tenant: </span>
            {row.tenant_name ?? "none"}
          </span>
          {row.carpet_area_sqm !== null ? (
            <span className="tabular-nums text-muted-foreground">
              {formatNumber(row.carpet_area_sqm)} m² carpet
            </span>
          ) : null}
          {row.last_verified_at ? (
            <span className="text-muted-foreground">
              Verified {formatDate(row.last_verified_at)}
            </span>
          ) : null}
        </div>

        {row.matched_on ? (
          <p className="text-xs text-muted-foreground">
            Matched on <span className="font-medium text-foreground">{row.matched_on}</span>
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}

export default function SearchPage() {
  const router = useRouter();
  const searchParams = useSearchParams();

  const q = searchParams.get("q") ?? "";
  const mode = (searchParams.get("mode") as SearchMode | null) ?? "auto";
  const page = Number(searchParams.get("page") ?? 1) || 1;
  const pageSize = Number(searchParams.get("page_size") ?? 20) || 20;
  const filters = React.useMemo(
    () => readFilters(new URLSearchParams(searchParams.toString())),
    [searchParams],
  );

  // The box is local so typing does not push a history entry per keystroke;
  // it is committed to the URL on submit.
  const [draft, setDraft] = React.useState(q);
  React.useEffect(() => setDraft(q), [q]);

  const [railOpen, setRailOpen] = React.useState(false);

  const push = React.useCallback(
    (next: Record<string, string | number | boolean | undefined>) => {
      const sp = new URLSearchParams(searchParams.toString());
      for (const [key, value] of Object.entries(next)) {
        if (value === undefined || value === "" || value === false) sp.delete(key);
        else sp.set(key, value === true ? "1" : String(value));
      }
      // Any change other than paging returns to the first page, because page 4
      // of a different query is a different, meaningless place.
      if (!("page" in next)) sp.delete("page");
      router.push(`/search?${sp.toString()}`, { scroll: false });
    },
    [router, searchParams],
  );

  const enabled = q.trim().length > 0;

  const { data, isLoading, isFetching, isError, error, refetch } = useQuery({
    queryKey: ["search", q, mode, page, pageSize, filters],
    queryFn: () => search.run({ q, mode, page, page_size: pageSize, ...filters }),
    enabled,
    placeholderData: (prev) => prev,
  });

  // Suggestions for the raw draft, not the committed query — they only help
  // while the user is still deciding what to ask.
  const { data: suggestions } = useQuery({
    queryKey: ["search-suggest", draft],
    queryFn: () => search.suggest(draft, 8),
    enabled: draft.trim().length >= 2 && draft !== q,
    staleTime: 30_000,
  });

  // `pages` is a Python @property on the response model and so never reaches
  // the wire — derive it here rather than reading an undefined field.
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const filterCount = activeFilterCount(filters);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Search the register"
        description="By ULPIN, owner name, tenant name or building name."
      />

      <form
        onSubmit={(e) => {
          e.preventDefault();
          push({ q: draft.trim(), page: undefined });
        }}
        className="space-y-3"
      >
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="relative flex-1">
            <SearchIcon
              className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              placeholder="WB-KOL-B001-F03-U301, Rina Banerjee, Maitri Heights…"
              aria-label="Search query"
              className="pl-9"
              autoComplete="off"
              list="search-suggestions"
            />
            <datalist id="search-suggestions">
              {suggestions?.items.map((s) => (
                <option key={`${s.kind}:${s.value}`} value={s.value}>
                  {s.hint ? `${s.label} — ${s.hint}` : s.label}
                </option>
              ))}
            </datalist>
          </div>

          <Select value={mode} onValueChange={(v) => push({ mode: v === "auto" ? undefined : v })}>
            <SelectTrigger className="sm:w-44" aria-label="Search mode">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {SEARCH_MODES.map((m) => (
                <SelectItem key={m.value} value={m.value}>
                  {m.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Button type="submit" loading={enabled && isFetching}>
            <SearchIcon /> Search
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => setRailOpen((o) => !o)}
            aria-expanded={railOpen}
          >
            <Filter /> Filters
            {filterCount > 0 ? (
              <Badge variant="secondary" className="ml-1 tabular-nums">
                {filterCount}
              </Badge>
            ) : null}
          </Button>
        </div>

        {railOpen ? (
          <Card>
            <CardContent className="grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-4">
              <div className="space-y-1.5">
                <Label htmlFor="f-type">Property type</Label>
                <Select
                  value={filters.unit_type ?? ANY}
                  onValueChange={(v) => push({ unit_type: v === ANY ? undefined : v })}
                >
                  <SelectTrigger id="f-type">
                    <SelectValue placeholder="Any" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ANY}>Any type</SelectItem>
                    {PROPERTY_TYPES.map((t) => (
                      <SelectItem key={t.value} value={t.value}>
                        {t.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="f-occ">Occupancy</Label>
                <Select
                  value={filters.occupancy_status ?? ANY}
                  onValueChange={(v) => push({ occupancy_status: v === ANY ? undefined : v })}
                >
                  <SelectTrigger id="f-occ">
                    <SelectValue placeholder="Any" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ANY}>Any status</SelectItem>
                    {OCCUPANCY_STATUSES.map((t) => (
                      <SelectItem key={t.value} value={t.value}>
                        {t.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="f-out">Verification</Label>
                <Select
                  value={filters.verification_outcome ?? ANY}
                  onValueChange={(v) => push({ verification_outcome: v === ANY ? undefined : v })}
                >
                  <SelectTrigger id="f-out">
                    <SelectValue placeholder="Any" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ANY}>Any outcome</SelectItem>
                    {VERIFICATION_OUTCOMES.map((t) => (
                      <SelectItem key={t.value} value={t.value}>
                        {t.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="f-state">State</Label>
                <Select
                  value={filters.state ?? ANY}
                  onValueChange={(v) => push({ state: v === ANY ? undefined : v })}
                >
                  <SelectTrigger id="f-state">
                    <SelectValue placeholder="Any" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ANY}>Any state</SelectItem>
                    {STATE_CODES.map((s) => (
                      <SelectItem key={s.value} value={s.value}>
                        {s.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="f-city">City</Label>
                <Input
                  id="f-city"
                  defaultValue={filters.city ?? ""}
                  placeholder="Kolkata"
                  onBlur={(e) => push({ city: e.target.value.trim() || undefined })}
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="f-fmin">Floor from</Label>
                <Input
                  id="f-fmin"
                  type="number"
                  defaultValue={filters.floor_number_min ?? ""}
                  placeholder="-2"
                  onBlur={(e) => push({ floor_number_min: e.target.value || undefined })}
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="f-fmax">Floor to</Label>
                <Input
                  id="f-fmax"
                  type="number"
                  defaultValue={filters.floor_number_max ?? ""}
                  placeholder="24"
                  onBlur={(e) => push({ floor_number_max: e.target.value || undefined })}
                />
              </div>

              <div className="flex items-end justify-between gap-3">
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="size-4 rounded border-input accent-[hsl(var(--status-fraud))]"
                    checked={filters.has_open_alerts === true}
                    onChange={(e) => push({ has_open_alerts: e.target.checked || undefined })}
                  />
                  Flagged only
                </label>
                {filterCount > 0 ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      push({
                        unit_type: undefined,
                        occupancy_status: undefined,
                        verification_outcome: undefined,
                        city: undefined,
                        state: undefined,
                        floor_number_min: undefined,
                        floor_number_max: undefined,
                        has_open_alerts: undefined,
                      })
                    }
                  >
                    <X /> Clear
                  </Button>
                ) : null}
              </div>
            </CardContent>
          </Card>
        ) : null}
      </form>

      {!enabled ? (
        <EmptyState
          icon={SearchIcon}
          title="Enter something to search for"
          description="An identifier in either form, a person's name, or a building. Partial names work — matching is fuzzy, so a misremembered spelling still finds the row."
        />
      ) : isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isLoading ? (
        <SkeletonCards count={5} className="h-28" />
      ) : (data?.items.length ?? 0) === 0 ? (
        <EmptyState
          icon={SearchIcon}
          title="Nothing matched"
          description={
            filterCount > 0
              ? "No row matches both the query and the filters. Clearing the filters is usually the quicker test."
              : "No unit, identifier, owner or tenant matches that. Check the identifier's prefix, or try a shorter fragment of the name."
          }
        />
      ) : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className={cn("text-sm text-muted-foreground", isFetching && "opacity-60")}>
              <span className="font-medium text-foreground tabular-nums">
                {formatNumber(data!.total)}
              </span>{" "}
              {data!.total === 1 ? "match" : "matches"}
              {data!.mode_used !== "auto" || mode === "auto" ? (
                <> · searched as {data!.mode_used}</>
              ) : null}
              {data!.took_ms !== null ? <> · {data!.took_ms} ms</> : null}
            </p>
          </div>

          <div className="space-y-3">
            {data!.items.map((row) => (
              <ResultCard key={row.unit_id} row={row} />
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
