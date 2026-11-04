import { useState, type ReactNode } from "react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { useCost } from "../api/hooks";
import { Card, EmptyState, ErrorState, LoadingState, PageHeader, cx } from "../components/ui";
import { usd } from "../lib/format";

interface Datum {
  name: string;
  value: number;
}

const AXIS = { fontSize: 12, fill: "var(--ink-2)" };

function TipBox({ active, payload, label, fmt }: { active?: boolean; payload?: { value: number; payload: Datum & { note?: string } }[]; label?: string; fmt: (v: number) => string }) {
  if (!active || !payload?.length) return null;
  const p = payload[0];
  return (
    <div className="rounded-lg border border-line bg-surface px-3 py-2 text-sm shadow-card">
      <div className="font-medium text-ink">{label ?? p.payload.name}</div>
      <div className="tabular text-ink-2">{fmt(p.value)}</div>
      {p.payload.note && <div className="text-[0.75rem] text-ink-3">{p.payload.note}</div>}
    </div>
  );
}

function ChartCard({ title, caption, data, fmt, children, empty }: { title: string; caption: string; data: Datum[]; fmt: (v: number) => string; children: ReactNode; empty: string }) {
  const [table, setTable] = useState(false);
  return (
    <Card
      title={title}
      aside={
        data.length > 0 && (
          <button type="button" onClick={() => setTable((t) => !t)} aria-pressed={table} className="rounded-md px-2 py-1 text-[0.75rem] font-medium text-accent hover:bg-accent-soft">
            {table ? "Show chart" : "Show table"}
          </button>
        )
      }
    >
      <p className="mb-3 text-[0.8125rem] text-ink-2">{caption}</p>
      {data.length === 0 ? (
        <p className="py-10 text-center text-sm text-ink-3">{empty}</p>
      ) : table ? (
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-[0.75rem] text-ink-3">
              <th className="py-1.5 font-medium">Name</th>
              <th className="py-1.5 text-right font-medium">Value</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d) => (
              <tr key={d.name} className="border-b border-line last:border-0">
                <td className="py-1.5 text-ink">{d.name}</td>
                <td className="tabular py-1.5 text-right text-ink-2">{fmt(d.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="h-64" role="img" aria-label={`${title}: ${data.map((d) => `${d.name} ${fmt(d.value)}`).join(", ")}`}>
          {children}
        </div>
      )}
    </Card>
  );
}

function Bars({ data, fmt }: { data: Datum[]; fmt: (v: number) => string }) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16 }}>
        <CartesianGrid horizontal={false} stroke="var(--border)" />
        <XAxis type="number" tickFormatter={fmt} tick={AXIS} axisLine={false} tickLine={false} />
        <YAxis type="category" dataKey="name" width={118} tick={AXIS} axisLine={false} tickLine={false} />
        <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<TipBox fmt={fmt} />} />
        <Bar dataKey="value" fill="var(--accent)" radius={[0, 4, 4, 0]} barSize={16} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4 shadow-card">
      <div className="text-[0.75rem] tracking-wide text-ink-3 uppercase">{k}</div>
      <div className="tabular mt-1 text-[1.75rem] leading-none font-semibold text-ink">{v}</div>
    </div>
  );
}

export default function CostDashboard() {
  const [days, setDays] = useState(30);
  const q = useCost(days);
  const d = q.data;
  const pct = (v: number) => `${Math.round(v * 100)}%`;

  const perType: Datum[] = (d?.per_task_type ?? []).map((t) => ({ name: t.task_type.replace(/_/g, " "), value: t.cost_usd, note: `${t.tasks} runs · avg ${usd(t.avg_cost_usd)}` })) as Datum[];
  const agents: Datum[] = (d?.top_agents ?? []).map((a) => ({ name: a.agent, value: a.cost_usd, note: `${a.calls} calls` })) as Datum[];
  const tools: Datum[] = (d?.tool_usage ?? []).map((t) => ({ name: t.tool, value: t.calls, note: `${t.errors} errors · avg ${Math.round(t.avg_latency_ms)} ms` })) as Datum[];
  const trend: Datum[] = (d?.escalation_trend ?? []).map((e) => ({ name: e.day, value: e.rate, note: `${e.escalated} of ${e.tasks} runs escalated` })) as Datum[];

  const totalCost = perType.reduce((s, x) => s + x.value, 0);
  const totalRuns = (d?.per_task_type ?? []).reduce((s, x) => s + x.tasks, 0);
  const escalated = (d?.escalation_trend ?? []).reduce((s, x) => s + x.escalated, 0);

  return (
    <>
      <PageHeader
        title="Cost"
        subtitle="Where tokens and tool calls go. Prices are notional (free-tier models bill nothing); cached calls count as zero."
        actions={
          <div role="radiogroup" aria-label="Period" className="inline-flex rounded-lg border border-line bg-surface p-0.5 text-sm">
            {[7, 30, 90].map((n) => (
              <button key={n} role="radio" aria-checked={days === n} onClick={() => setDays(n)} className={cx("rounded-md px-3 py-1 font-medium", days === n ? "bg-accent text-accent-ink" : "text-ink-2 hover:text-ink")}>
                {n} days
              </button>
            ))}
          </div>
        }
      />
      {q.isLoading && <LoadingState rows={4} label="Loading cost data" />}
      {q.isError && <ErrorState error={q.error} retry={() => q.refetch()} />}
      {d && totalRuns === 0 && <EmptyState title="No runs in this period" hint="Run a review and its cost shows up here." />}
      {d && totalRuns > 0 && (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <Stat k="Total cost" v={usd(totalCost)} />
            <Stat k="Runs" v={String(totalRuns)} />
            <Stat k="Avg per run" v={usd(totalRuns ? totalCost / totalRuns : 0)} />
            <Stat k="Escalated" v={`${escalated} of ${totalRuns}`} />
          </div>
          <div className="grid gap-5 xl:grid-cols-2">
            <ChartCard title="Cost per task type" caption="Total spend by kind of run." data={perType} fmt={usd} empty="No data yet.">
              <Bars data={perType} fmt={usd} />
            </ChartCard>
            <ChartCard title="Most expensive agents" caption="Spend by agent, highest first." data={agents} fmt={usd} empty="No data yet.">
              <Bars data={agents} fmt={usd} />
            </ChartCard>
            <ChartCard title="Tool usage" caption="Calls per tool." data={tools} fmt={(v) => String(Math.round(v))} empty="No tool calls yet.">
              <Bars data={tools} fmt={(v) => String(Math.round(v))} />
            </ChartCard>
            <ChartCard title="Escalation rate" caption="Share of runs that needed a human, per day." data={trend} fmt={pct} empty="No data yet.">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={trend} margin={{ left: 0, right: 16, top: 8 }}>
                  <CartesianGrid vertical={false} stroke="var(--border)" />
                  <XAxis dataKey="name" tick={AXIS} axisLine={false} tickLine={false} />
                  <YAxis domain={[0, 1]} tickFormatter={pct} tick={AXIS} axisLine={false} tickLine={false} width={44} />
                  <Tooltip content={<TipBox fmt={pct} />} />
                  <Line type="monotone" dataKey="value" stroke="var(--accent)" strokeWidth={2} dot={{ r: 4, fill: "var(--accent)", stroke: "var(--surface)", strokeWidth: 2 }} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </ChartCard>
          </div>
        </div>
      )}
    </>
  );
}
