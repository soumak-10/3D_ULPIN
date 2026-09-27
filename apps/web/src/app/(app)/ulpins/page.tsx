"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, Fingerprint, XCircle } from "lucide-react";
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
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ulpins } from "@/lib/api/endpoints";
import { formatDate } from "@/lib/utils";

/**
 * The ULPIN registry (Request F, read side).
 *
 * Both identifiers are shown on every row. They are not derivable from one
 * another — the short code is the quotable public form, the 14-character code
 * is what the parcel register and every downstream system key on — so hiding
 * either would make one of the two systems unsearchable from here.
 */
export default function UlpinsPage() {
  const router = useRouter();
  const sp = useSearchParams();

  const page = Number(sp.get("page") ?? 1) || 1;
  const pageSize = Number(sp.get("page_size") ?? 20) || 20;
  const q = sp.get("q") ?? "";

  const [draft, setDraft] = React.useState(q);
  React.useEffect(() => setDraft(q), [q]);

  const [candidate, setCandidate] = React.useState("");

  const push = (next: Record<string, string | number | undefined>) => {
    const params = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(next)) {
      if (v === undefined || v === "") params.delete(k);
      else params.set(k, String(v));
    }
    if (!("page" in next)) params.delete("page");
    router.push(`/ulpins?${params.toString()}`, { scroll: false });
  };

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["ulpins", { page, pageSize, q }],
    queryFn: () => ulpins.list({ page, page_size: pageSize, q: q || undefined }),
    placeholderData: (prev) => prev,
  });

  const validate = useMutation({ mutationFn: (code: string) => ulpins.validate(code) });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div className="space-y-6">
      <PageHeader
        title="ULPIN registry"
        description="Every identifier issued, in both its public and its parcel-derived form."
      />

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Check an identifier</CardTitle>
          <CardDescription>
            Structural validation only — this says whether a code is well-formed, not whether the
            property behind it is sound. Use verification for that.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <form
            className="flex flex-col gap-2 sm:flex-row"
            onSubmit={(e) => {
              e.preventDefault();
              if (candidate.trim()) validate.mutate(candidate.trim().toUpperCase());
            }}
          >
            <div className="flex-1 space-y-1.5">
              <Label htmlFor="candidate" className="sr-only">
                Identifier
              </Label>
              <Input
                id="candidate"
                value={candidate}
                onChange={(e) => setCandidate(e.target.value)}
                placeholder="WB-KOL-B001-F03-U301"
                className="ulpin uppercase"
                autoComplete="off"
              />
            </div>
            <Button type="submit" variant="outline" loading={validate.isPending}>
              Validate
            </Button>
          </form>

          {validate.data ? (
            <div
              className={
                validate.data.valid
                  ? "flex items-start gap-2 rounded-md border border-verified/40 bg-verified/10 p-3 text-sm"
                  : "flex items-start gap-2 rounded-md border border-fraud/40 bg-fraud/10 p-3 text-sm"
              }
            >
              {validate.data.valid ? (
                <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-verified" aria-hidden />
              ) : (
                <XCircle className="mt-0.5 size-4 shrink-0 text-fraud" aria-hidden />
              )}
              <div className="min-w-0 space-y-1">
                <p className="font-medium">
                  {validate.data.valid ? "Well-formed" : "Not a valid identifier"}
                </p>
                {validate.data.errors.length > 0 ? (
                  <ul className="list-inside list-disc text-muted-foreground">
                    {validate.data.errors.map((e) => (
                      <li key={e}>{e}</li>
                    ))}
                  </ul>
                ) : null}
                {validate.data.parsed ? (
                  <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
                    {Object.entries(validate.data.parsed).map(([k, v]) => (
                      <div key={k} className="flex gap-1">
                        <dt className="font-medium">{k.replace(/_/g, " ")}:</dt>
                        <dd className="ulpin">{String(v)}</dd>
                      </div>
                    ))}
                  </dl>
                ) : null}
              </div>
            </div>
          ) : null}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="gap-3 pb-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1">
              <CardTitle>Issued identifiers</CardTitle>
              <CardDescription>Newest first</CardDescription>
            </div>
            <form
              onSubmit={(e) => {
                e.preventDefault();
                push({ q: draft.trim() || undefined });
              }}
            >
              <Input
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="Filter by code"
                className="w-full sm:w-64"
                aria-label="Filter identifiers"
              />
            </form>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 pt-0">
          {isError ? (
            <ErrorState error={error} onRetry={() => void refetch()} />
          ) : isLoading ? (
            <Skeleton className="h-64" />
          ) : (data?.items.length ?? 0) === 0 ? (
            <EmptyState
              icon={Fingerprint}
              title="No identifiers issued"
              description="Identifiers are issued when units are registered. Add a unit to a building to see one here."
            />
          ) : (
            <>
              <div className="overflow-x-auto">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Short code</TableHead>
                      <TableHead>Parcel code</TableHead>
                      <TableHead>Floor</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead className="text-right">Version</TableHead>
                      <TableHead>Issued</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data?.items.map((u) => (
                      <TableRow key={u.ulpin_id}>
                        <TableCell>
                          <UlpinChip code={u.short_code} />
                        </TableCell>
                        <TableCell>
                          <span className="ulpin text-xs text-muted-foreground">
                            {u.ulpin_code}
                          </span>
                        </TableCell>
                        <TableCell className="tabular-nums text-muted-foreground">
                          {u.floor_number}
                          <span className="pl-1 text-xs">
                            ({u.storey_class.toLowerCase()})
                          </span>
                        </TableCell>
                        <TableCell>
                          <Badge variant={u.status === "ACTIVE" ? "verified" : "outline"}>
                            {u.status.toLowerCase()}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-right tabular-nums text-muted-foreground">
                          v{u.version}
                        </TableCell>
                        <TableCell className="whitespace-nowrap text-muted-foreground">
                          {formatDate(u.issued_at)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              <Pagination
                page={data!.page}
                pageSize={data!.page_size}
                total={data!.total}
                pages={totalPages}
                onPageChange={(p) => push({ page: p })}
                onPageSizeChange={(s) => push({ page_size: s, page: undefined })}
              />
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
