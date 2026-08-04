import type { StrategyStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

const labels: Record<StrategyStatus, string> = {
  running: "运行中",
  paused: "已暂停",
  backtest: "回测中",
  draft: "草稿",
};

export function StatusPill({ status }: { status: StrategyStatus }) {
  return (
    <span
      className={cn(
        "inline-flex items-center text-xs tracking-wide",
        status === "running" && "text-[var(--gain)]",
        status === "paused" && "text-[var(--signal)]",
        status === "backtest" && "text-[var(--teal)]",
        status === "draft" && "text-[var(--ink-soft)]/50",
      )}
    >
      <span
        className={cn(
          "mr-1.5 inline-block h-1.5 w-1.5 rounded-full",
          status === "running" && "bg-[var(--gain)]",
          status === "paused" && "bg-[var(--signal)]",
          status === "backtest" && "bg-[var(--teal)]",
          status === "draft" && "bg-[var(--ink-soft)]/40",
        )}
      />
      {labels[status]}
    </span>
  );
}
