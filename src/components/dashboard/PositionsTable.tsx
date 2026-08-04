import type { Position } from "@/lib/types";
import { formatPct, formatUsd } from "@/lib/utils";

export function PositionsTable({ positions }: { positions: Position[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] text-left text-sm">
        <thead className="text-xs uppercase tracking-wider text-[var(--ink-soft)]/45">
          <tr className="border-b border-[var(--line)]">
            <th className="py-3 font-medium">交易对</th>
            <th className="py-3 font-medium">方向</th>
            <th className="py-3 font-medium">数量</th>
            <th className="py-3 font-medium">开仓价</th>
            <th className="py-3 font-medium">标记价</th>
            <th className="py-3 font-medium">杠杆</th>
            <th className="py-3 font-medium">未实现盈亏</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => (
            <tr key={p.id} className="border-b border-[var(--line)]/70">
              <td className="py-3.5 font-medium text-[var(--ink)]">{p.symbol}</td>
              <td className={p.side === "long" ? "text-[var(--gain)]" : "text-[var(--loss)]"}>
                {p.side === "long" ? "多" : "空"}
              </td>
              <td className="font-mono-num">{p.size}</td>
              <td className="font-mono-num">{formatUsd(p.entryPrice)}</td>
              <td className="font-mono-num">{formatUsd(p.markPrice)}</td>
              <td className="font-mono-num">{p.leverage}x</td>
              <td
                className={`font-mono-num ${p.pnl >= 0 ? "text-[var(--gain)]" : "text-[var(--loss)]"}`}
              >
                {formatUsd(p.pnl)} ({formatPct(p.pnlPct)})
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
