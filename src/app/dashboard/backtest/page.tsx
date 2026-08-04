import { EquityChart } from "@/components/dashboard/EquityChart";
import { backtests } from "@/lib/data";
import { formatPct } from "@/lib/utils";

export default function BacktestPage() {
  const primary = backtests[0];

  return (
    <div className="space-y-10">
      <header>
        <p className="text-sm tracking-[0.18em] text-[var(--teal)]">BACKTEST</p>
        <h1 className="mt-2 font-display text-3xl text-[var(--ink)] md:text-4xl">回测结果</h1>
        <p className="mt-2 text-sm text-[var(--ink-soft)]/65">
          历史区间压力测试样本，后续可接入自定义参数与批量扫描。
        </p>
      </header>

      {primary ? (
        <section>
          <div className="mb-4 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
            <div>
              <h2 className="font-display text-xl">{primary.strategyName}</h2>
              <p className="mt-1 text-sm text-[var(--ink-soft)]/55">
                {primary.pair} · {primary.period}
              </p>
            </div>
            <div className="flex flex-wrap gap-6 text-sm">
              <span className="font-mono-num text-[var(--gain)]">
                收益 {formatPct(primary.totalReturn)}
              </span>
              <span className="font-mono-num">夏普 {primary.sharpe.toFixed(2)}</span>
              <span className="font-mono-num">回撤 {formatPct(primary.maxDrawdown)}</span>
            </div>
          </div>
          <EquityChart data={primary.equity} />
        </section>
      ) : null}

      <section className="divide-y divide-[var(--line)] border-y border-[var(--line)]">
        {backtests.map((bt) => (
          <article
            key={bt.id}
            className="grid gap-4 py-6 md:grid-cols-[1.4fr_repeat(5,1fr)] md:items-center"
          >
            <div>
              <h3 className="font-display text-lg">{bt.strategyName}</h3>
              <p className="mt-1 text-xs text-[var(--ink-soft)]/50">
                {bt.pair} · {bt.period}
              </p>
            </div>
            <Metric label="收益" value={formatPct(bt.totalReturn)} gain />
            <Metric label="夏普" value={bt.sharpe.toFixed(2)} />
            <Metric label="回撤" value={formatPct(bt.maxDrawdown)} />
            <Metric label="胜率" value={`${bt.winRate.toFixed(1)}%`} />
            <Metric label="成交笔数" value={String(bt.trades)} />
          </article>
        ))}
      </section>
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
    <div>
      <p className="text-xs uppercase tracking-wider text-[var(--ink-soft)]/45">{label}</p>
      <p className={`mt-1 font-mono-num ${gain ? "text-[var(--gain)]" : ""}`}>{value}</p>
    </div>
  );
}
