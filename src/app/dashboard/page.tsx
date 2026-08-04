import { EquityChart } from "@/components/dashboard/EquityChart";
import { PositionsTable } from "@/components/dashboard/PositionsTable";
import { StatBlock } from "@/components/dashboard/StatBlock";
import { StatusPill } from "@/components/dashboard/StatusPill";
import { TradesTable } from "@/components/dashboard/TradesTable";
import { portfolio, strategies } from "@/lib/data";
import { formatPct, formatUsd } from "@/lib/utils";

export default function DashboardPage() {
  const running = strategies.filter((s) => s.status === "running").length;

  return (
    <div className="space-y-10">
      <header>
        <p className="text-sm tracking-[0.18em] text-[var(--teal)]">OVERVIEW</p>
        <h1 className="mt-2 font-display text-3xl text-[var(--ink)] md:text-4xl">账户总览</h1>
        <p className="mt-2 text-sm text-[var(--ink-soft)]/65">
          模拟账户数据，后续可接入交易所 API 与实盘引擎。
        </p>
      </header>

      <section className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
        <StatBlock label="总权益" value={formatUsd(portfolio.totalEquity)} />
        <StatBlock label="可用余额" value={formatUsd(portfolio.availableBalance)} />
        <StatBlock
          label="今日盈亏"
          value={`${formatUsd(portfolio.todayPnl)} (${formatPct(portfolio.todayPnlPct)})`}
          tone={portfolio.todayPnl >= 0 ? "gain" : "loss"}
        />
        <StatBlock
          label="未实现盈亏"
          value={formatUsd(portfolio.unrealizedPnl)}
          tone={portfolio.unrealizedPnl >= 0 ? "gain" : "loss"}
          hint={`${running} 个策略运行中`}
        />
      </section>

      <section>
        <div className="mb-4 flex items-end justify-between">
          <h2 className="font-display text-xl text-[var(--ink)]">净值曲线</h2>
          <p className="text-xs text-[var(--ink-soft)]/50">策略净值 vs 买入持有基准</p>
        </div>
        <EquityChart data={portfolio.equityCurve} />
      </section>

      <section className="grid gap-10 lg:grid-cols-2">
        <div>
          <h2 className="mb-4 font-display text-xl text-[var(--ink)]">运行策略</h2>
          <div className="divide-y divide-[var(--line)] border-y border-[var(--line)]">
            {strategies.slice(0, 4).map((s) => (
              <div key={s.id} className="flex items-center justify-between gap-4 py-4">
                <div>
                  <p className="font-medium text-[var(--ink)]">{s.name}</p>
                  <p className="mt-1 text-xs text-[var(--ink-soft)]/50">
                    {s.pair} · {s.timeframe}
                  </p>
                </div>
                <div className="text-right">
                  <StatusPill status={s.status} />
                  <p className="mt-1 font-mono-num text-sm text-[var(--gain)]">
                    {formatPct(s.totalReturn)}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div>
          <h2 className="mb-4 font-display text-xl text-[var(--ink)]">最近成交</h2>
          <TradesTable trades={portfolio.recentTrades.slice(0, 5)} />
        </div>
      </section>

      <section>
        <h2 className="mb-4 font-display text-xl text-[var(--ink)]">当前持仓</h2>
        <PositionsTable positions={portfolio.positions} />
      </section>
    </div>
  );
}
