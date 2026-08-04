import type { Trade } from "@/lib/types";
import { formatUsd } from "@/lib/utils";

export function TradesTable({ trades }: { trades: Trade[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-left text-sm">
        <thead className="text-xs uppercase tracking-wider text-[var(--ink-soft)]/45">
          <tr className="border-b border-[var(--line)]">
            <th className="py-3 font-medium">时间</th>
            <th className="py-3 font-medium">交易对</th>
            <th className="py-3 font-medium">方向</th>
            <th className="py-3 font-medium">价格</th>
            <th className="py-3 font-medium">数量</th>
            <th className="py-3 font-medium">盈亏</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t) => (
            <tr key={t.id} className="border-b border-[var(--line)]/70">
              <td className="py-3.5 font-mono-num text-[var(--ink-soft)]/70">
                {new Date(t.time).toLocaleString("zh-CN", { hour12: false })}
              </td>
              <td className="font-medium">{t.symbol}</td>
              <td className={t.side === "long" ? "text-[var(--gain)]" : "text-[var(--loss)]"}>
                {t.side === "long" ? "买入" : "卖出"}
              </td>
              <td className="font-mono-num">{formatUsd(t.price)}</td>
              <td className="font-mono-num">{t.quantity}</td>
              <td
                className={`font-mono-num ${t.pnl >= 0 ? "text-[var(--gain)]" : "text-[var(--loss)]"}`}
              >
                {formatUsd(t.pnl)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
