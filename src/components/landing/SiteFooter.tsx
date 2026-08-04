import Link from "next/link";

export function SiteFooter() {
  return (
    <footer className="border-t border-white/10 bg-[var(--ink)] text-white">
      <div className="mx-auto flex max-w-6xl flex-col gap-6 px-5 py-12 md:flex-row md:items-center md:justify-between md:px-8">
        <div>
          <p className="font-display text-xl tracking-[0.18em]">XINING</p>
          <p className="mt-2 text-sm text-white/50">加密货币量化交易平台 · 研究到执行的统一入口</p>
        </div>
        <div className="flex gap-6 text-sm text-white/60">
          <Link href="/dashboard" className="hover:text-white">
            控制台
          </Link>
          <Link href="/dashboard/strategies" className="hover:text-white">
            策略
          </Link>
          <Link href="/dashboard/backtest" className="hover:text-white">
            回测
          </Link>
        </div>
      </div>
    </footer>
  );
}
