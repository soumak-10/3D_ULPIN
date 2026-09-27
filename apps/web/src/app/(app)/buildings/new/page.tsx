"use client";

import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { zodResolver } from "@hookform/resolvers/zod";
import { ArrowLeft, Building2, MapPin } from "lucide-react";
import { useForm } from "react-hook-form";
import { toast } from "sonner";

import { PageHeader } from "@/components/shared";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, fieldProps } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ApiError, errorMessage } from "@/lib/api/client";
import { buildings } from "@/lib/api/endpoints";
import { STATE_CODES } from "@/lib/constants";
import { buildingSchema, clean, type BuildingValues } from "@/lib/validators";

/**
 * Register a building (Request E).
 *
 * The five fields the brief names — name, address, latitude, longitude, total
 * floors — are required; everything else is optional and grouped below, because
 * a registration form that demands the year of construction before it will
 * accept a tower is a form people work around rather than fill in.
 */
export default function NewBuildingPage() {
  const router = useRouter();

  const form = useForm<BuildingValues>({
    resolver: zodResolver(buildingSchema),
    defaultValues: {
      building_name: "",
      address_line1: "",
      address_line2: "",
      locality: "",
      city: "",
      district: "",
      state_code: "WB",
      pincode: "",
      basement_floors: 0,
      generate_floors: true,
    },
  });

  const create = useMutation({
    mutationFn: (values: BuildingValues) => buildings.create(clean(values)),
    onSuccess: (b) => {
      toast.success(`${b.building_name} registered.`, {
        description: "Add units next — each one gets its own identifier.",
      });
      router.push(`/buildings/${b.building_id}`);
    },
    onError: (e) => {
      if (e instanceof ApiError && e.fieldErrors) {
        for (const [field, message] of Object.entries(e.fieldErrors)) {
          form.setError(field as keyof BuildingValues, { message });
        }
        return;
      }
      toast.error(errorMessage(e));
    },
  });

  const errors = form.formState.errors;

  return (
    <div className="space-y-6">
      <Button variant="ghost" size="sm" className="-ml-2" onClick={() => router.back()}>
        <ArrowLeft /> Back
      </Button>

      <PageHeader
        title="Register a building"
        description="The structure first. Floors and units are added to it afterwards."
      />

      <form
        onSubmit={form.handleSubmit((v) => create.mutate(v))}
        className="max-w-3xl space-y-4"
        noValidate
      >
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Building2 className="size-4" aria-hidden /> Identity
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Field
              label="Building name"
              htmlFor="building_name"
              required
              className="sm:col-span-2"
              error={errors.building_name?.message}
            >
              <Input
                {...form.register("building_name")}
                {...fieldProps("building_name", errors.building_name?.message)}
                placeholder="Maitri Heights"
                autoFocus
              />
            </Field>

            <Field
              label="Total floors"
              htmlFor="total_floors"
              required
              hint="Above ground."
              error={errors.total_floors?.message}
            >
              <Input
                type="number"
                {...form.register("total_floors")}
                {...fieldProps("total_floors", errors.total_floors?.message)}
                placeholder="12"
              />
            </Field>

            <Field
              label="Basement floors"
              htmlFor="basement_floors"
              error={errors.basement_floors?.message}
            >
              <Input type="number" {...form.register("basement_floors")} placeholder="2" />
            </Field>

            <Field label="Building height (m)" htmlFor="building_height_m">
              <Input type="number" step="0.1" {...form.register("building_height_m")} />
            </Field>

            <Field label="Year built" htmlFor="year_built" error={errors.year_built?.message}>
              <Input type="number" {...form.register("year_built")} placeholder="2019" />
            </Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <MapPin className="size-4" aria-hidden /> Location
            </CardTitle>
            <CardDescription>
              The coordinates anchor the parcel. They are checked against India&apos;s bounding box
              on the way in, so a transposed pair is rejected rather than stored.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-2">
            <Field
              label="Address"
              htmlFor="address_line1"
              required
              className="sm:col-span-2"
              error={errors.address_line1?.message}
            >
              <Input
                {...form.register("address_line1")}
                {...fieldProps("address_line1", errors.address_line1?.message)}
                placeholder="14 Rajdanga Main Road"
              />
            </Field>

            <Field label="Address line 2" htmlFor="address_line2" className="sm:col-span-2">
              <Input {...form.register("address_line2")} />
            </Field>

            <Field label="Locality" htmlFor="locality">
              <Input {...form.register("locality")} placeholder="Kasba" />
            </Field>

            <Field label="City" htmlFor="city" required error={errors.city?.message}>
              <Input
                {...form.register("city")}
                {...fieldProps("city", errors.city?.message)}
                placeholder="Kolkata"
              />
            </Field>

            <Field label="District" htmlFor="district">
              <Input {...form.register("district")} placeholder="South 24 Parganas" />
            </Field>

            <Field label="State" htmlFor="state_code" required error={errors.state_code?.message}>
              <Select
                value={form.watch("state_code")}
                onValueChange={(v) => form.setValue("state_code", v, { shouldValidate: true })}
              >
                <SelectTrigger id="state_code">
                  <SelectValue placeholder="Select a state" />
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
              <Input
                {...form.register("pincode")}
                {...fieldProps("pincode", errors.pincode?.message)}
                inputMode="numeric"
                placeholder="700107"
              />
            </Field>

            <Field label="Latitude" htmlFor="latitude" required error={errors.latitude?.message}>
              <Input
                type="number"
                step="0.000001"
                {...form.register("latitude")}
                {...fieldProps("latitude", errors.latitude?.message)}
                placeholder="22.512300"
              />
            </Field>

            <Field label="Longitude" htmlFor="longitude" required error={errors.longitude?.message}>
              <Input
                type="number"
                step="0.000001"
                {...form.register("longitude")}
                {...fieldProps("longitude", errors.longitude?.message)}
                placeholder="88.394500"
              />
            </Field>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                className="mt-0.5 size-4 rounded border-input"
                {...form.register("generate_floors")}
              />
              <span>
                Create the floor records automatically
                <span className="block text-xs text-muted-foreground">
                  One row per storey, basements numbered negative and ground as zero. Clear this
                  only if the building&apos;s storeys do not run consecutively.
                </span>
              </span>
            </label>
          </CardContent>
        </Card>

        <div className="flex gap-2">
          <Button type="submit" loading={create.isPending}>
            Register building
          </Button>
          <Button type="button" variant="ghost" onClick={() => router.back()}>
            Cancel
          </Button>
        </div>
      </form>
    </div>
  );
}
