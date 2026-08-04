import Link from "next/link";
import { strategies } from "@/lib/data";
import { formatPct, formatUsd } from "@/lib/utils";

const preview = strategies.filter((s) => s.status === "running" || s.status === "paused").slice(0, 3);

export function StrategiesPreview() {
  return (
    <section id="strategies" className="bg-[var(--ink)] py-24 text-white md:py-32">
      <div className="mx-auto max-w-6xl px-5 md:px-8">
        <div className="flex flex-col justify-between gap-6 md:flex-row md:items-end">
          <div>
            <p className="text-sm tracking-[0.2em] text-[var(--teal-bright)]">STRATEGIES</p>
            <h2 className="mt-3 font-display text-3xl md:text-5xl">运行中的策略样本</h2>
            <p className="mt-4 max-w-xl text-white/65">
              以实盘风格展示收益、回撤与资金分配，后续可对接交易所与自建引擎。
            </p>
          </div>
          <Link
            href="/dashboard/strategies"
            className="text-sm text-[var(--signal-soft)] transition hover:text-white"
          >
            查看全部策略 →
          </Link>
        </div>

        <div className="mt-12 divide-y divide-white/10 border-y border-white/10">
          {preview.map((strategy) => (
            <div
              key={strategy.id}
              className="grid gap-4 py-6 md:grid-cols-[1.4fr_repeat(4,1fr)] md:items-center"
            >
              <div>
                <p className="font-display text-lg">{strategy.name}</p>
                <p className="mt-1 text-sm text-white/50">
                  {strategy.pair} · {strategy.timeframe}
                </p>
              </div>
              <Metric label="累计收益" value={formatPct(strategy.totalReturn)} positive />
              <Metric label="夏普" value={strategy.sharpe.toFixed(2)} />
              <Metric label="最大回撤" value={formatPct(strategy.maxDrawdown)} />
              <Metric label="分配资金" value={formatUsd(strategy.allocated, 0)} />
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  positive,
}: {
  label: string;
  value: string;
  positive?: boolean;
}) {
  return (
    <div>
      <p className="text-xs uppercase tracking-wider text-white/40">{label}</p>
      <p className={`mt-1 font-mono-num text-base ${positive ? "text-[var(--teal-bright)]" : ""}`}>
        {value}
      </p>
    </div>
  );
}
