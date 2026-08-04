import { StatusPill } from "@/components/dashboard/StatusPill";
import { strategies } from "@/lib/data";
import { formatPct, formatUsd } from "@/lib/utils";

export default function StrategiesPage() {
  return (
    <div className="space-y-10">
      <header>
        <p className="text-sm tracking-[0.18em] text-[var(--teal)]">STRATEGIES</p>
        <h1 className="mt-2 font-display text-3xl text-[var(--ink)] md:text-4xl">策略管理</h1>
        <p className="mt-2 text-sm text-[var(--ink-soft)]/65">
          管理策略状态、资金分配与核心绩效指标。
        </p>
      </header>

      <div className="divide-y divide-[var(--line)] border-y border-[var(--line)]">
        {strategies.map((s) => (
          <article key={s.id} className="grid gap-5 py-7 lg:grid-cols-[1.5fr_2fr]">
            <div>
              <div className="flex flex-wrap items-center gap-3">
                <h2 className="font-display text-xl text-[var(--ink)]">{s.name}</h2>
                <StatusPill status={s.status} />
              </div>
              <p className="mt-2 text-sm leading-relaxed text-[var(--ink-soft)]/70">
                {s.description}
              </p>
              <p className="mt-3 text-xs text-[var(--ink-soft)]/45">
                {s.pair} · {s.timeframe} · 更新于{" "}
                {new Date(s.updatedAt).toLocaleString("zh-CN", { hour12: false })}
              </p>
            </div>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
              <Metric label="累计收益" value={formatPct(s.totalReturn)} gain={s.totalReturn >= 0} />
              <Metric label="夏普比率" value={s.sharpe.toFixed(2)} />
              <Metric label="最大回撤" value={formatPct(s.maxDrawdown)} />
              <Metric label="胜率" value={`${s.winRate.toFixed(1)}%`} />
              <Metric label="分配资金" value={formatUsd(s.allocated, 0)} />
              <Metric
                label="状态操作"
                value={s.status === "running" ? "可暂停" : s.status === "paused" ? "可启动" : "待部署"}
              />
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  gain,
}: {
  label: string;
  value: string;
  gain?: boolean;
}) {
  return (
    <div className="border-t border-[var(--line)] pt-3">
      <p className="text-xs uppercase tracking-wider text-[var(--ink-soft)]/45">{label}</p>
      <p className={`mt-1 font-mono-num text-base ${gain ? "text-[var(--gain)]" : "text-[var(--ink)]"}`}>
        {value}
      </p>
    </div>
  );
}
