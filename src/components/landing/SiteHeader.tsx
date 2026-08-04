import Link from "next/link";

export function SiteHeader() {
  return (
    <header className="absolute inset-x-0 top-0 z-20">
      <div className="mx-auto flex max-w-6xl items-center justify-between px-5 py-5 md:px-8">
        <Link href="/" className="font-display text-lg tracking-[0.18em] text-white">
          XINING
        </Link>
        <nav className="hidden items-center gap-8 text-sm text-white/70 md:flex">
          <a href="#system" className="transition hover:text-white">
            系统
          </a>
          <a href="#strategies" className="transition hover:text-white">
            策略
          </a>
          <a href="#workflow" className="transition hover:text-white">
            流程
          </a>
          <Link href="/dashboard" className="transition hover:text-white">
            控制台
          </Link>
        </nav>
        <Link
          href="/dashboard"
          className="rounded-sm bg-[var(--signal)] px-4 py-2 text-sm font-medium text-white transition hover:bg-[var(--signal-soft)]"
        >
          进入交易台
        </Link>
      </div>
    </header>
  );
}
