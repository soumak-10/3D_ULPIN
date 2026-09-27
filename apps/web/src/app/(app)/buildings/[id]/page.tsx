"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowLeft, Box, Fingerprint, MapPin, Plus, UserPlus } from "lucide-react";
import * as React from "react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import {
  EmptyState,
  ErrorState,
  PageHeader,
  StatusBadge,
  UlpinChip,
} from "@/components/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, errorMessage } from "@/lib/api/client";
import { buildings, owners, units as unitsApi } from "@/lib/api/endpoints";
import { CORE_PROPERTY_TYPES, OCCUPANCY_STATUSES, OWNERSHIP_MODES } from "@/lib/constants";
import { formatDate, formatNumber } from "@/lib/utils";
import {
  clean,
  ownershipSchema,
  tenantSchema,
  unitSchema,
  type OwnershipValues,
  type TenantValues,
  type UnitValues,
} from "@/lib/validators";
import { useAuth } from "@/providers/auth-provider";

/**
 * One building: its floors, its units, and the forms that add to both
 * (Request E).
 *
 * Units, ownership and tenancy are created under their parent rather than at a
 * top-level collection, matching the API — a unit without a building and a
 * tenancy without a unit are not things the register can hold.
 */
export default function BuildingDetailPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const qc = useQueryClient();
  const { isStaff } = useAuth();

  const [unitOpen, setUnitOpen] = React.useState(false);
  const [target, setTarget] = React.useState<{ unitId: string; label: string } | null>(null);
  const [tenantFor, setTenantFor] = React.useState<{ unitId: string; label: string } | null>(null);

  const building = useQuery({ queryKey: ["building", id], queryFn: () => buildings.get(id) });
  const floors = useQuery({ queryKey: ["building-floors", id], queryFn: () => buildings.floors(id) });
  const units = useQuery({
    queryKey: ["building-units", id],
    queryFn: () => buildings.units(id, { page: 1, page_size: 200 }),
  });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["building-units", id] });
    void qc.invalidateQueries({ queryKey: ["building", id] });
  };

  /* ------------------------------------------------------------- unit form -- */

  const unitForm = useForm<UnitValues>({
    resolver: zodResolver(unitSchema),
    defaultValues: {
      unit_number: "",
      property_type: "RESIDENTIAL",
      occupancy_status: "VACANT",
      generate_ulpin: true,
    },
  });

  const createUnit = useMutation({
    mutationFn: (values: UnitValues) => buildings.createUnit(id, clean(values)),
    onSuccess: (u) => {
      toast.success(`Unit ${u.unit_number} registered.`, {
        description: u.short_code ? `Identifier ${u.short_code}` : "No identifier issued yet.",
      });
      setUnitOpen(false);
      unitForm.reset({
        unit_number: "",
        property_type: "RESIDENTIAL",
        occupancy_status: "VACANT",
        generate_ulpin: true,
      });
      invalidate();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.fieldErrors) {
        for (const [f, m] of Object.entries(e.fieldErrors)) {
          unitForm.setError(f as keyof UnitValues, { message: m });
        }
        return;
      }
      toast.error(errorMessage(e));
    },
  });

  /* -------------------------------------------------------- ownership form -- */

  const ownerList = useQuery({
    queryKey: ["owners", "picker"],
    queryFn: () => owners.list({ page: 1, page_size: 200 }),
    enabled: Boolean(target),
  });

  const ownershipForm = useForm<OwnershipValues>({
    resolver: zodResolver(ownershipSchema),
    defaultValues: {
      ownership_mode: "SOLE",
      share_fraction: 1,
      is_primary: true,
      acquired_on: new Date().toISOString().slice(0, 10),
    },
  });

  const addOwner = useMutation({
    mutationFn: (values: OwnershipValues) => unitsApi.addOwnership(target!.unitId, clean(values)),
    onSuccess: () => {
      toast.success("Ownership recorded.");
      setTarget(null);
      ownershipForm.reset();
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  /* ----------------------------------------------------------- tenant form -- */

  const tenantForm = useForm<TenantValues>({
    resolver: zodResolver(tenantSchema),
    defaultValues: { full_name: "", lease_start: new Date().toISOString().slice(0, 10) },
  });

  const addTenant = useMutation({
    mutationFn: (values: TenantValues) => unitsApi.addTenancy(tenantFor!.unitId, clean(values)),
    onSuccess: () => {
      toast.success("Tenancy recorded.", { description: "The unit is now marked occupied." });
      setTenantFor(null);
      tenantForm.reset({ full_name: "", lease_start: new Date().toISOString().slice(0, 10) });
      invalidate();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (building.isError) {
    return (
      <div className="space-y-6">
        <PageHeader title="Building" />
        <ErrorState error={building.error} onRetry={() => void building.refetch()} />
      </div>
    );
  }

  if (building.isLoading || !building.data) {
    return (
      <div className="space-y-6">
        <Skeleton className="h-10 w-80" />
        <Skeleton className="h-64" />
      </div>
    );
  }

  const b = building.data;
  const floorOptions = (floors.data ?? []).slice().sort((x, y) => y.floor_number - x.floor_number);

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" className="-ml-2" onClick={() => router.back()}>
        <ArrowLeft /> Back
      </Button>

      <PageHeader title={b.building_name} description={`${b.address_line1}, ${b.city}`}>
        <Button variant="outline" asChild>
          <Link href={`/viewer?building=${b.building_id}`}>
            <Box /> View in 3D
          </Link>
        </Button>
        {isStaff ? (
          <Dialog open={unitOpen} onOpenChange={setUnitOpen}>
            <DialogTrigger asChild>
              <Button>
                <Plus /> Add unit
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-lg">
              <DialogHeader>
                <DialogTitle>Register a unit</DialogTitle>
                <DialogDescription>
                  In {b.building_name}. An identifier is issued on creation unless you clear the
                  box below.
                </DialogDescription>
              </DialogHeader>
              <form
                id="unit-form"
                onSubmit={unitForm.handleSubmit((v) => createUnit.mutate(v))}
                className="grid gap-4 sm:grid-cols-2"
                noValidate
              >
                <Field
                  label="Floor number"
                  htmlFor="floor_number"
                  required
                  error={unitForm.formState.errors.floor_number?.message}
                >
                  <Input
                    type="number"
                    {...unitForm.register("floor_number")}
                    {...fieldProps("floor_number", unitForm.formState.errors.floor_number?.message)}
                    placeholder="3"
                  />
                </Field>

                <Field
                  label="Unit number"
                  htmlFor="unit_number"
                  required
                  error={unitForm.formState.errors.unit_number?.message}
                >
                  <Input
                    {...unitForm.register("unit_number")}
                    {...fieldProps("unit_number", unitForm.formState.errors.unit_number?.message)}
                    placeholder="301"
                  />
                </Field>

                <Field label="Property type" htmlFor="property_type" required>
                  <Select
                    value={unitForm.watch("property_type")}
                    onValueChange={(v) =>
                      unitForm.setValue("property_type", v as UnitValues["property_type"])
                    }
                  >
                    <SelectTrigger id="property_type">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {CORE_PROPERTY_TYPES.map((t) => (
                        <SelectItem key={t.value} value={t.value}>
                          {t.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>

                <Field label="Status" htmlFor="occupancy_status" required>
                  <Select
                    value={unitForm.watch("occupancy_status")}
                    onValueChange={(v) =>
                      unitForm.setValue("occupancy_status", v as UnitValues["occupancy_status"])
                    }
                  >
                    <SelectTrigger id="occupancy_status">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {OCCUPANCY_STATUSES.map((t) => (
                        <SelectItem key={t.value} value={t.value}>
                          {t.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>

                <Field label="Carpet area (m²)" htmlFor="carpet_area_sqm">
                  <Input type="number" step="0.01" {...unitForm.register("carpet_area_sqm")} />
                </Field>

                <Field label="Built-up area (m²)" htmlFor="built_up_area_sqm">
                  <Input type="number" step="0.01" {...unitForm.register("built_up_area_sqm")} />
                </Field>

                <label className="flex items-start gap-2 text-sm sm:col-span-2">
                  <input
                    type="checkbox"
                    className="mt-0.5 size-4 rounded border-input"
                    {...unitForm.register("generate_ulpin")}
                  />
                  <span>
                    Issue a ULPIN now
                    <span className="block text-xs text-muted-foreground">
                      Format STATE-CITY-BUILDING-FLOOR-UNIT, e.g. WB-KOL-B001-F03-U301.
                    </span>
                  </span>
                </label>
              </form>
              <DialogFooter>
                <Button type="submit" form="unit-form" loading={createUnit.isPending}>
                  Register unit
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        ) : null}
      </PageHeader>

      <Card>
        <CardContent className="grid gap-x-6 gap-y-3 p-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Code</p>
            <p className="pt-0.5">
              {b.short_code ? <UlpinChip code={b.short_code} /> : "not assigned"}
            </p>
          </div>
          <div>
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Floors</p>
            <p className="pt-0.5 tabular-nums">
              {b.total_floors}
              {b.basement_floors > 0 ? ` +${b.basement_floors} basement` : ""}
            </p>
          </div>
          <div>
            <p className="text-xs uppercase tracking-wide text-muted-foreground">Units</p>
            <p className="pt-0.5 tabular-nums">{formatNumber(units.data?.total ?? 0)}</p>
          </div>
          <div>
            <p className="flex items-center gap-1 text-xs uppercase tracking-wide text-muted-foreground">
              <MapPin className="size-3" aria-hidden /> Coordinates
            </p>
            <p className="ulpin pt-0.5 text-xs">
              {b.latitude.toFixed(6)}, {b.longitude.toFixed(6)}
            </p>
          </div>
        </CardContent>
      </Card>

      <Tabs defaultValue="units">
        <TabsList>
          <TabsTrigger value="units">Units</TabsTrigger>
          <TabsTrigger value="floors">Floors</TabsTrigger>
        </TabsList>

        <TabsContent value="units" className="pt-4">
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Units</CardTitle>
              <CardDescription>
                Every individually-owned volume in this structure, with its identifier
              </CardDescription>
            </CardHeader>
            <CardContent className="pt-0">
              {units.isLoading ? (
                <Skeleton className="h-48" />
              ) : (units.data?.items.length ?? 0) === 0 ? (
                <EmptyState
                  icon={Box}
                  title="No units yet"
                  description="Add the first unit and the register issues its identifier automatically."
                />
              ) : (
                <div className="overflow-x-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Unit</TableHead>
                        <TableHead>Floor</TableHead>
                        <TableHead>Type</TableHead>
                        <TableHead>ULPIN</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Verification</TableHead>
                        {isStaff ? <TableHead className="text-right">Record</TableHead> : null}
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {units.data?.items.map((u) => (
                        <TableRow key={u.unit_id}>
                          <TableCell className="font-medium">{u.unit_number}</TableCell>
                          <TableCell className="tabular-nums text-muted-foreground">
                            {u.floor_number}
                          </TableCell>
                          <TableCell className="text-muted-foreground">
                            {u.property_type.replace(/_/g, " ").toLowerCase()}
                          </TableCell>
                          <TableCell>
                            <UlpinChip code={u.short_code ?? u.ulpin_code} />
                          </TableCell>
                          <TableCell>
                            <Badge variant="outline">
                              {u.occupancy_status.replace(/_/g, " ").toLowerCase()}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <StatusBadge outcome={u.verification_outcome} />
                          </TableCell>
                          {isStaff ? (
                            <TableCell className="whitespace-nowrap text-right">
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() =>
                                  setTarget({ unitId: u.unit_id, label: u.unit_number })
                                }
                              >
                                <UserPlus /> Owner
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() =>
                                  setTenantFor({ unitId: u.unit_id, label: u.unit_number })
                                }
                              >
                                Tenant
                              </Button>
                            </TableCell>
                          ) : null}
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="floors" className="pt-4">
          <Card>
            <CardContent className="pt-6">
              {floors.isLoading ? (
                <Skeleton className="h-40" />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Floor</TableHead>
                      <TableHead>Label</TableHead>
                      <TableHead>Class</TableHead>
                      <TableHead>Elevation</TableHead>
                      <TableHead className="text-right">Units</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {floorOptions.map((f) => (
                      <TableRow key={f.floor_id}>
                        <TableCell className="font-medium tabular-nums">{f.floor_number}</TableCell>
                        <TableCell>{f.floor_label ?? "—"}</TableCell>
                        <TableCell className="text-muted-foreground">
                          {f.storey_class.replace(/_/g, " ").toLowerCase()}
                        </TableCell>
                        <TableCell className="tabular-nums text-muted-foreground">
                          {f.elevation_m !== null ? `${f.elevation_m} m` : "—"}
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          {f.unit_count ?? 0}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* Ownership */}
      <Dialog open={Boolean(target)} onOpenChange={(o) => !o && setTarget(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Record ownership</DialogTitle>
            <DialogDescription>Unit {target?.label}</DialogDescription>
          </DialogHeader>
          <form
            id="ownership-form"
            onSubmit={ownershipForm.handleSubmit((v) => addOwner.mutate(v))}
            className="grid gap-4 sm:grid-cols-2"
            noValidate
          >
            <Field
              label="Owner"
              htmlFor="owner_id"
              required
              className="sm:col-span-2"
              error={ownershipForm.formState.errors.owner_id?.message}
              hint="Not listed? Create the owner from the owners page first."
            >
              <Select
                value={ownershipForm.watch("owner_id")}
                onValueChange={(v) =>
                  ownershipForm.setValue("owner_id", v, { shouldValidate: true })
                }
              >
                <SelectTrigger id="owner_id">
                  <SelectValue placeholder="Select an owner" />
                </SelectTrigger>
                <SelectContent>
                  {ownerList.data?.items.map((o) => (
                    <SelectItem key={o.owner_id} value={o.owner_id}>
                      {o.display_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            <Field label="Mode" htmlFor="ownership_mode" required>
              <Select
                value={ownershipForm.watch("ownership_mode")}
                onValueChange={(v) =>
                  ownershipForm.setValue("ownership_mode", v as OwnershipValues["ownership_mode"])
                }
              >
                <SelectTrigger id="ownership_mode">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {OWNERSHIP_MODES.map((m) => (
                    <SelectItem key={m.value} value={m.value}>
                      {m.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            <Field
              label="Share"
              htmlFor="share_fraction"
              hint="1 for sole ownership, 0.5 for a half share."
              error={ownershipForm.formState.errors.share_fraction?.message}
            >
              <Input type="number" step="0.01" {...ownershipForm.register("share_fraction")} />
            </Field>

            <Field
              label="Acquired on"
              htmlFor="acquired_on"
              required
              error={ownershipForm.formState.errors.acquired_on?.message}
            >
              <Input type="date" {...ownershipForm.register("acquired_on")} />
            </Field>

            <Field label="Deed number" htmlFor="deed_number">
              <Input {...ownershipForm.register("deed_number")} />
            </Field>
          </form>
          <DialogFooter>
            <Button type="submit" form="ownership-form" loading={addOwner.isPending}>
              Record ownership
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Tenancy */}
      <Dialog open={Boolean(tenantFor)} onOpenChange={(o) => !o && setTenantFor(null)}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Record a tenancy</DialogTitle>
            <DialogDescription>Unit {tenantFor?.label}</DialogDescription>
          </DialogHeader>
          <form
            id="tenant-form"
            onSubmit={tenantForm.handleSubmit((v) => addTenant.mutate(v))}
            className="grid gap-4 sm:grid-cols-2"
            noValidate
          >
            <Field
              label="Tenant name"
              htmlFor="full_name"
              required
              className="sm:col-span-2"
              error={tenantForm.formState.errors.full_name?.message}
            >
              <Input
                {...tenantForm.register("full_name")}
                {...fieldProps("full_name", tenantForm.formState.errors.full_name?.message)}
                placeholder="Arjun Mehta"
              />
            </Field>

            <Field label="Email" htmlFor="email" error={tenantForm.formState.errors.email?.message}>
              <Input type="email" {...tenantForm.register("email")} />
            </Field>

            <Field label="Phone" htmlFor="phone" error={tenantForm.formState.errors.phone?.message}>
              <Input {...tenantForm.register("phone")} placeholder="9876543210" />
            </Field>

            <Field
              label="Lease start"
              htmlFor="lease_start"
              required
              error={tenantForm.formState.errors.lease_start?.message}
            >
              <Input type="date" {...tenantForm.register("lease_start")} />
            </Field>

            <Field
              label="Lease end"
              htmlFor="lease_end"
              error={tenantForm.formState.errors.lease_end?.message}
            >
              <Input type="date" {...tenantForm.register("lease_end")} />
            </Field>

            <Field label="Monthly rent (₹)" htmlFor="monthly_rent">
              <Input type="number" {...tenantForm.register("monthly_rent")} />
            </Field>

            <Field label="Deposit (₹)" htmlFor="deposit_amount">
              <Input type="number" {...tenantForm.register("deposit_amount")} />
            </Field>

            <Field label="Agreement number" htmlFor="agreement_number" className="sm:col-span-2">
              <Input {...tenantForm.register("agreement_number")} />
            </Field>
          </form>
          <DialogFooter>
            <Button type="submit" form="tenant-form" loading={addTenant.isPending}>
              Record tenancy
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <p className="flex items-start gap-2 text-xs text-muted-foreground">
        <Fingerprint className="mt-0.5 size-3.5 shrink-0" aria-hidden />
        Registered {formatDate(b.created_at)}. Identifiers shown here are the short public form;
        the 14-character parcel form is stored alongside each one.
      </p>
    </div>
  );
}
