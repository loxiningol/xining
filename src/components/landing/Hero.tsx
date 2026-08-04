import Link from "next/link";

export function Hero() {
  return (
    <section className="relative min-h-[100svh] overflow-hidden atmosphere text-white">
      <div className="pointer-events-none absolute inset-0 grid-overlay" />
      <div className="pointer-events-none absolute -right-24 top-24 h-[520px] w-[520px] rounded-full bg-[radial-gradient(circle,rgba(20,160,122,0.35),transparent_65%)] animate-drift" />
      <div className="pointer-events-none absolute -left-20 bottom-10 h-[380px] w-[380px] rounded-full bg-[radial-gradient(circle,rgba(217,119,6,0.22),transparent_70%)] animate-drift" />

      <svg
        className="pointer-events-none absolute inset-x-0 bottom-0 h-[42%] w-full opacity-70 animate-pulse-line"
        viewBox="0 0 1440 420"
        preserveAspectRatio="none"
        aria-hidden
      >
        <path
          d="M0,280 C160,220 260,340 420,250 C580,160 700,300 860,210 C1020,120 1140,250 1280,190 C1360,155 1400,170 1440,150 L1440,420 L0,420 Z"
          fill="url(#heroFill)"
        />
        <path
          d="M0,280 C160,220 260,340 420,250 C580,160 700,300 860,210 C1020,120 1140,250 1280,190 C1360,155 1400,170 1440,150"
          fill="none"
          stroke="#14a07a"
          strokeWidth="2.5"
        />
        <defs>
          <linearGradient id="heroFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#14a07a" stopOpacity="0.35" />
            <stop offset="100%" stopColor="#14a07a" stopOpacity="0" />
          </linearGradient>
        </defs>
      </svg>

      <div className="relative z-10 mx-auto flex min-h-[100svh] max-w-6xl flex-col justify-center px-5 pb-24 pt-28 md:px-8">
        <p className="font-display animate-rise text-5xl tracking-[0.22em] text-white md:text-7xl lg:text-8xl">
          XINING
        </p>
        <h1 className="mt-6 max-w-2xl animate-rise-delay-1 font-display text-2xl leading-snug text-white/95 md:text-4xl">
          加密货币量化交易系统
        </h1>
        <p className="mt-5 max-w-xl animate-rise-delay-1 text-base leading-relaxed text-white/70 md:text-lg">
          策略研发、回测验证与实盘执行一体，用纪律化引擎捕捉市场结构机会。
        </p>
        <div className="mt-10 flex flex-wrap items-center gap-4 animate-rise-delay-2">
          <Link
            href="/dashboard"
            className="rounded-sm bg-[var(--signal)] px-6 py-3 text-sm font-semibold text-white transition hover:bg-[var(--signal-soft)]"
          >
            打开控制台
          </Link>
          <a
            href="#system"
            className="rounded-sm border border-white/25 px-6 py-3 text-sm font-medium text-white/90 transition hover:border-white/50 hover:bg-white/5"
          >
            了解系统
          </a>
        </div>
      </div>
    </section>
  );
}
