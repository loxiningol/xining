import { PositionsTable } from "@/components/dashboard/PositionsTable";
import { StatBlock } from "@/components/dashboard/StatBlock";
import { TradesTable } from "@/components/dashboard/TradesTable";
import { portfolio } from "@/lib/data";
import { formatPct, formatUsd } from "@/lib/utils";

export default function PortfolioPage() {
  const exposure = portfolio.positions.reduce(
    (sum, p) => sum + Math.abs(p.size * p.markPrice),
    0,
  );

  return (
    <div className="space-y-10">
      <header>
        <p className="text-sm tracking-[0.18em] text-[var(--teal)]">PORTFOLIO</p>
        <h1 className="mt-2 font-display text-3xl text-[var(--ink)] md:text-4xl">持仓与成交</h1>
        <p className="mt-2 text-sm text-[var(--ink-soft)]/65">
          跟踪敞口、杠杆与逐笔盈亏，便于风控复盘。
        </p>
      </header>

      <section className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
        <StatBlock label="总权益" value={formatUsd(portfolio.totalEquity)} />
        <StatBlock label="名义敞口" value={formatUsd(exposure, 0)} />
        <StatBlock
          label="未实现盈亏"
          value={formatUsd(portfolio.unrealizedPnl)}
          tone={portfolio.unrealizedPnl >= 0 ? "gain" : "loss"}
        />
        <StatBlock
          label="今日盈亏"
          value={formatPct(portfolio.todayPnlPct)}
          tone={portfolio.todayPnlPct >= 0 ? "gain" : "loss"}
        />
      </section>

      <section>
        <h2 className="mb-4 font-display text-xl">持仓明细</h2>
        <PositionsTable positions={portfolio.positions} />
      </section>

      <section>
        <h2 className="mb-4 font-display text-xl">成交记录</h2>
        <TradesTable trades={portfolio.recentTrades} />
      </section>
    </div>
  );
}
