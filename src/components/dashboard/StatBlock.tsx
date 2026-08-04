import { cn } from "@/lib/utils";

export function StatBlock({
  label,
  value,
  hint,
  tone = "neutral",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "neutral" | "gain" | "loss";
}) {
  return (
    <div className="border-t border-[var(--line)] pt-4">
      <p className="text-xs uppercase tracking-wider text-[var(--ink-soft)]/50">{label}</p>
      <p
        className={cn(
          "mt-2 font-mono-num text-2xl",
          tone === "gain" && "text-[var(--gain)]",
          tone === "loss" && "text-[var(--loss)]",
          tone === "neutral" && "text-[var(--ink)]",
        )}
      >
        {value}
      </p>
      {hint ? <p className="mt-1 text-xs text-[var(--ink-soft)]/55">{hint}</p> : null}
    </div>
  );
}
