"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import type { OccupancyPoint, SeriesPoint, TrendPoint } from "@/types/api";

/**
 * The dashboard charts (Request K).
 *
 * All four take data already shaped by the API — `GET /dashboard` returns one
 * object per day with severity counts as keys, precisely so the client does no
 * pivoting. Aggregation in the browser means the chart disagrees with the
 * cards on the same page the moment a page size changes.
 */

const AXIS = {
  stroke: "hsl(var(--muted-foreground))",
  fontSize: 11,
  tickLine: false,
  axisLine: false,
};

const TOOLTIP_STYLE = {
  backgroundColor: "hsl(var(--popover))",
  border: "1px solid hsl(var(--border))",
  borderRadius: "0.5rem",
  fontSize: "0.8125rem",
  color: "hsl(var(--popover-foreground))",
};

/** Resolved from the CSS variables so charts and badges cannot drift apart. */
const COLOURS = {
  verified: "hsl(var(--status-verified))",
  pending: "hsl(var(--status-pending))",
  fraud: "hsl(var(--status-fraud))",
  primary: "hsl(var(--primary))",
  muted: "hsl(var(--muted-foreground))",
};

const TONE_FILL: Record<string, string> = {
  verified: COLOURS.verified,
  pending: COLOURS.pending,
  fraud: COLOURS.fraud,
  neutral: COLOURS.muted,
};

/** A distinct-but-related ramp for the property-type pie. */
const PIE_FILLS = [
  "hsl(217 60% 36%)",
  "hsl(199 70% 40%)",
  "hsl(172 55% 34%)",
  "hsl(262 45% 48%)",
  "hsl(38 70% 42%)",
  "hsl(280 30% 50%)",
  "hsl(215 20% 50%)",
  "hsl(150 25% 40%)",
];

function ChartCard({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle>{title}</CardTitle>
        {description ? <CardDescription>{description}</CardDescription> : null}
      </CardHeader>
      <CardContent className="pt-2">
        <div className="h-[16rem] w-full">{children}</div>
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------- property distribution -- */

export function PropertyDistributionChart({ data }: { data: SeriesPoint[] }) {
  if (data.length === 0) {
    return (
      <ChartCard title="Property distribution" description="Units by property type">
        <EmptyChart />
      </ChartCard>
    );
  }

  return (
    <ChartCard title="Property distribution" description="Registered units by property type">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            innerRadius="52%"
            outerRadius="80%"
            paddingAngle={2}
            strokeWidth={1}
          >
            {data.map((entry, i) => (
              <Cell key={entry.name} fill={PIE_FILLS[i % PIE_FILLS.length]} />
            ))}
          </Pie>
          <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v: number) => [v, "Units"]} />
          <Legend
            verticalAlign="bottom"
            height={36}
            iconType="circle"
            iconSize={8}
            formatter={(value: string) => (
              <span className="text-xs text-muted-foreground">{value}</span>
            )}
          />
        </PieChart>
      </ResponsiveContainer>
    </ChartCard>
  );
}

/* --------------------------------------------------- verification status -- */

export function VerificationStatusChart({ data }: { data: SeriesPoint[] }) {
  return (
    <ChartCard
      title="Verification status"
      description="Green verified, yellow pending, red adverse"
    >
      {data.length === 0 ? (
        <EmptyChart />
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
            <XAxis dataKey="name" {...AXIS} interval={0} tickMargin={8} />
            <YAxis {...AXIS} allowDecimals={false} />
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              cursor={{ fill: "hsl(var(--accent))", opacity: 0.4 }}
              formatter={(v: number) => [v, "Units"]}
            />
            <Bar dataKey="value" radius={[4, 4, 0, 0]} maxBarSize={72}>
              {data.map((entry) => (
                <Cell key={entry.name} fill={TONE_FILL[entry.tone ?? "neutral"] ?? COLOURS.muted} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

/* --------------------------------------------------------------- fraud -- */

export function FraudTrendChart({ data }: { data: TrendPoint[] }) {
  return (
    <ChartCard title="Fraud trend" description="Alerts raised per day, by severity">
      {data.length === 0 ? (
        <EmptyChart />
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
            <defs>
              <linearGradient id="gradHigh" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={COLOURS.fraud} stopOpacity={0.7} />
                <stop offset="100%" stopColor={COLOURS.fraud} stopOpacity={0.05} />
              </linearGradient>
              <linearGradient id="gradMed" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={COLOURS.pending} stopOpacity={0.7} />
                <stop offset="100%" stopColor={COLOURS.pending} stopOpacity={0.05} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
            <XAxis dataKey="date" {...AXIS} tickMargin={8} minTickGap={24} />
            <YAxis {...AXIS} allowDecimals={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Legend
              verticalAlign="top"
              height={28}
              iconType="circle"
              iconSize={8}
              formatter={(value: string) => (
                <span className="text-xs capitalize text-muted-foreground">{value}</span>
              )}
            />
            {/* Stacked, because what an officer needs is "how many arrived",
                broken down — not four lines they have to add up by eye. */}
            <Area
              type="monotone"
              dataKey="low"
              stackId="1"
              stroke={COLOURS.muted}
              fill={COLOURS.muted}
              fillOpacity={0.18}
            />
            <Area
              type="monotone"
              dataKey="medium"
              stackId="1"
              stroke={COLOURS.pending}
              fill="url(#gradMed)"
            />
            <Area
              type="monotone"
              dataKey="high"
              stackId="1"
              stroke={COLOURS.fraud}
              fill="url(#gradHigh)"
            />
            <Area
              type="monotone"
              dataKey="critical"
              stackId="1"
              stroke="hsl(0 80% 30%)"
              fill="hsl(0 80% 30%)"
              fillOpacity={0.5}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

/* ----------------------------------------------------------- occupancy -- */

export function OccupancyTrendChart({ data }: { data: OccupancyPoint[] }) {
  return (
    <ChartCard
      title="Occupancy trend"
      description="Lease movements per day — the register keeps no daily occupancy history, so tenancy starts and ends stand in for it"
    >
      {data.length === 0 ? (
        <EmptyChart />
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
            <XAxis dataKey="date" {...AXIS} tickMargin={8} minTickGap={24} />
            <YAxis {...AXIS} allowDecimals={false} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            <Legend
              verticalAlign="top"
              height={28}
              iconType="circle"
              iconSize={8}
              formatter={(value: string) => (
                <span className="text-xs capitalize text-muted-foreground">{value}</span>
              )}
            />
            <Line
              type="monotone"
              dataKey="occupied"
              stroke={COLOURS.primary}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4 }}
            />
            <Line
              type="monotone"
              dataKey="vacant"
              stroke={COLOURS.muted}
              strokeWidth={2}
              strokeDasharray="4 4"
              dot={false}
              activeDot={{ r: 4 }}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </ChartCard>
  );
}

function EmptyChart() {
  return (
    <div className="flex h-full items-center justify-center rounded-md border border-dashed">
      <p className="text-sm text-muted-foreground">No data for this period yet.</p>
    </div>
  );
}
