"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { Mail, Phone, Plus, Users } from "lucide-react";
import { useForm } from "react-hook-form";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState, ErrorState, PageHeader, Pagination } from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Field, fieldProps } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { SkeletonRows } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError, errorMessage } from "@/lib/api/client";
import { owners } from "@/lib/api/endpoints";
import { OWNER_TYPES, STATE_CODES } from "@/lib/constants";
import { ownerSchema, clean, type OwnerValues } from "@/lib/validators";
import { useAuth } from "@/providers/auth-provider";

const ANY = "__any";
const ENTITY_TYPES = ["COMPANY", "TRUST", "SOCIETY", "GOVERNMENT"];

/**
 * The owner register (Request E, owner details).
 *
 * Owners are people and entities, not units — one owner holds many units and a
 * unit can be held jointly — so they live in their own register and are linked
 * to a unit from that unit's ownership record rather than typed in again.
 */
export default function OwnersPage() {
  const router = useRouter();
  const sp = useSearchParams();
  const { isStaff } = useAuth();
  const qc = useQueryClient();

  const page = Number(sp.get("page") ?? 1) || 1;
  const pageSize = Number(sp.get("page_size") ?? 20) || 20;
  const q = sp.get("q") ?? "";
  const ownerType = sp.get("owner_type") ?? undefined;

  const [draft, setDraft] = React.useState(q);
  React.useEffect(() => setDraft(q), [q]);

  const [open, setOpen] = React.useState(false);

  const push = (next: Record<string, string | number | undefined>) => {
    const params = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(next)) {
      if (v === undefined || v === "") params.delete(k);
      else params.set(k, String(v));
    }
    if (!("page" in next)) params.delete("page");
    router.push(`/owners?${params.toString()}`, { scroll: false });
  };

  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["owners", { page, pageSize, q, ownerType }],
    queryFn: () =>
      owners.list({ page, page_size: pageSize, q: q || undefined, owner_type: ownerType }),
    placeholderData: (prev) => prev,
  });

  const form = useForm<OwnerValues>({
    resolver: zodResolver(ownerSchema),
    defaultValues: {
      owner_type: "INDIVIDUAL",
      full_name: "",
      organisation_name: "",
      email: "",
      phone: "",
      address_line1: "",
      city: "",
      state_code: "WB",
      pincode: "",
      pan: "",
    },
  });

  const create = useMutation({
    mutationFn: (values: OwnerValues) => owners.create(clean(values)),
    onSuccess: (o) => {
      toast.success(`${o.display_name} added to the owner register.`);
      void qc.invalidateQueries({ queryKey: ["owners"] });
      setOpen(false);
      form.reset();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.fieldErrors) {
        for (const [field, message] of Object.entries(e.fieldErrors)) {
          form.setError(field as keyof OwnerValues, { message });
        }
        return;
      }
      toast.error(errorMessage(e));
    },
  });

  const type = form.watch("owner_type");
  const isEntity = ENTITY_TYPES.includes(type);
  const errors = form.formState.errors;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Owners"
        description="People and entities that hold property. Linked to units through ownership records."
      >
        {isStaff ? (
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button>
                <Plus /> Add owner
              </Button>
            </DialogTrigger>
            <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
              <DialogHeader>
                <DialogTitle>Add an owner</DialogTitle>
                <DialogDescription>
                  Identity only. Which units they hold, and in what share, is recorded on the unit.
                </DialogDescription>
              </DialogHeader>

              <form
                id="owner-form"
                onSubmit={form.handleSubmit((v) => create.mutate(v))}
                className="grid gap-4 sm:grid-cols-2"
                noValidate
              >
                <Field label="Owner type" htmlFor="owner_type" required className="sm:col-span-2">
                  <Select
                    value={type}
                    onValueChange={(v) =>
                      form.setValue("owner_type", v as OwnerValues["owner_type"], {
                        shouldValidate: true,
                      })
                    }
                  >
                    <SelectTrigger id="owner_type">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {OWNER_TYPES.map((t) => (
                        <SelectItem key={t.value} value={t.value}>
                          {t.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>

                {isEntity ? (
                  <Field
                    label="Organisation name"
                    htmlFor="organisation_name"
                    required
                    className="sm:col-span-2"
                    error={errors.organisation_name?.message ?? errors.full_name?.message}
                  >
                    <Input
                      {...form.register("organisation_name")}
                      {...fieldProps("organisation_name", errors.organisation_name?.message)}
                      placeholder="Maitri Housing Co-operative Society Ltd"
                    />
                  </Field>
                ) : (
                  <Field
                    label="Full name"
                    htmlFor="full_name"
                    required
                    className="sm:col-span-2"
                    error={errors.full_name?.message}
                  >
                    <Input
                      {...form.register("full_name")}
                      {...fieldProps("full_name", errors.full_name?.message)}
                      placeholder="Rina Banerjee"
                    />
                  </Field>
                )}

                <Field label="Email" htmlFor="email" error={errors.email?.message}>
                  <Input type="email" {...form.register("email")} autoComplete="off" />
                </Field>

                <Field label="Phone" htmlFor="phone" error={errors.phone?.message}>
                  <Input {...form.register("phone")} inputMode="tel" placeholder="9876543210" />
                </Field>

                <Field label="Address" htmlFor="address_line1" className="sm:col-span-2">
                  <Input {...form.register("address_line1")} />
                </Field>

                <Field label="City" htmlFor="city">
                  <Input {...form.register("city")} placeholder="Kolkata" />
                </Field>

                <Field label="State" htmlFor="state_code">
                  <Select
                    value={form.watch("state_code") || "WB"}
                    onValueChange={(v) => form.setValue("state_code", v)}
                  >
                    <SelectTrigger id="state_code">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {STATE_CODES.map((s) => (
                        <SelectItem key={s.value} value={s.value}>
                          {s.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>

                <Field label="PIN code" htmlFor="pincode" error={errors.pincode?.message}>
                  <Input {...form.register("pincode")} inputMode="numeric" placeholder="700107" />
                </Field>

                <Field
                  label="PAN"
                  htmlFor="pan"
                  hint="Optional, but it is what makes two owners with the same name distinguishable."
                  error={errors.pan?.message}
                >
                  <Input {...form.register("pan")} className="uppercase" placeholder="ABCDE1234F" />
                </Field>
              </form>

              <DialogFooter>
                <Button type="button" variant="ghost" onClick={() => setOpen(false)}>
                  Cancel
                </Button>
                <Button type="submit" form="owner-form" loading={create.isPending}>
                  Add owner
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
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
            placeholder="Name, email, phone or PAN"
            aria-label="Filter owners"
          />
        </form>
        <Select
          value={ownerType ?? ANY}
          onValueChange={(v) => push({ owner_type: v === ANY ? undefined : v })}
        >
          <SelectTrigger className="sm:w-48" aria-label="Owner type">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ANY}>All types</SelectItem>
            {OWNER_TYPES.map((t) => (
              <SelectItem key={t.value} value={t.value}>
                {t.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {isError ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : isLoading ? (
        <SkeletonRows rows={6} />
      ) : (data?.items.length ?? 0) === 0 ? (
        <EmptyState
          icon={Users}
          title={q || ownerType ? "No owner matches" : "No owners recorded"}
          description={
            q || ownerType
              ? "Clear the filters to see the whole register."
              : "Add an owner here, then link them to a unit from that unit's building page."
          }
        />
      ) : (
        <Card>
          <CardContent className="space-y-4 p-4">
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Name</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead>Contact</TableHead>
                    <TableHead>Location</TableHead>
                    <TableHead>KYC</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data?.items.map((o) => (
                    <TableRow key={o.owner_id}>
                      <TableCell className="font-medium">{o.display_name}</TableCell>
                      <TableCell className="text-muted-foreground">
                        {o.owner_type.toLowerCase()}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        <div className="space-y-0.5 text-xs">
                          {o.email ? (
                            <p className="flex items-center gap-1.5">
                              <Mail className="size-3 shrink-0" aria-hidden />
                              {o.email}
                            </p>
                          ) : null}
                          {o.phone ? (
                            <p className="flex items-center gap-1.5">
                              <Phone className="size-3 shrink-0" aria-hidden />
                              {o.phone}
                            </p>
                          ) : null}
                          {!o.email && !o.phone ? "—" : null}
                        </div>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {[o.city, o.state_code].filter(Boolean).join(", ") || "—"}
                      </TableCell>
                      <TableCell>
                        <Badge variant={o.kyc_status === "VERIFIED" ? "verified" : "pending"}>
                          {o.kyc_status.toLowerCase()}
                        </Badge>
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
          </CardContent>
        </Card>
      )}
    </div>
  );
}
