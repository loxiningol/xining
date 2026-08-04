import { markets } from "@/lib/data";
import { formatCompact, formatPct, formatUsd } from "@/lib/utils";

export default function MarketsPage() {
  return (
    <div className="space-y-10">
      <header>
        <p className="text-sm tracking-[0.18em] text-[var(--teal)]">MARKETS</p>
        <h1 className="mt-2 font-display text-3xl text-[var(--ink)] md:text-4xl">市场行情</h1>
        <p className="mt-2 text-sm text-[var(--ink-soft)]/65">
          关注价格、24h 涨跌、成交量与资金费率，为策略调度提供上下文。
        </p>
      </header>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead className="text-xs uppercase tracking-wider text-[var(--ink-soft)]/45">
            <tr className="border-b border-[var(--line)]">
              <th className="py-3 font-medium">交易对</th>
              <th className="py-3 font-medium">最新价</th>
              <th className="py-3 font-medium">24h 涨跌</th>
              <th className="py-3 font-medium">24h 成交额</th>
              <th className="py-3 font-medium">资金费率</th>
            </tr>
          </thead>
          <tbody>
            {markets.map((m) => (
              <tr key={m.symbol} className="border-b border-[var(--line)]/70">
                <td className="py-4 font-medium text-[var(--ink)]">{m.symbol}</td>
                <td className="font-mono-num">{formatUsd(m.price, m.price < 10 ? 4 : 2)}</td>
                <td
                  className={`font-mono-num ${m.change24h >= 0 ? "text-[var(--gain)]" : "text-[var(--loss)]"}`}
                >
                  {formatPct(m.change24h)}
                </td>
                <td className="font-mono-num">${formatCompact(m.volume24h)}</td>
                <td
                  className={`font-mono-num ${m.fundingRate >= 0 ? "text-[var(--gain)]" : "text-[var(--loss)]"}`}
                >
                  {formatPct(m.fundingRate, 4)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
