const pillars = [
  {
    title: "因子与信号",
    body: "动量、均值回归、资金费率与流动性因子统一编排，形成可复用信号层。",
  },
  {
    title: "回测与风控",
    body: "手续费、滑点与爆仓约束纳入模拟，输出夏普、回撤与胜率等核心指标。",
  },
  {
    title: "实盘执行",
    body: "策略启停、仓位分配与成交归因实时可见，让执行过程可审计、可迭代。",
  },
];

export function SystemSection() {
  return (
    <section id="system" className="atmosphere-light border-t border-[var(--line)] py-24 md:py-32">
      <div className="mx-auto max-w-6xl px-5 md:px-8">
        <p className="text-sm tracking-[0.2em] text-[var(--teal)]">SYSTEM</p>
        <h2 className="mt-3 max-w-2xl font-display text-3xl text-[var(--ink)] md:text-5xl">
          一套为加密市场设计的量化工作台
        </h2>
        <p className="mt-4 max-w-2xl text-[var(--ink-soft)]/80">
          从研究到执行缩短反馈环，让每一次迭代都建立在可验证的数据之上。
        </p>

        <div className="mt-14 grid gap-10 md:grid-cols-3">
          {pillars.map((item) => (
            <article key={item.title} className="border-t border-[var(--ink)]/15 pt-6">
              <h3 className="font-display text-xl text-[var(--ink)]">{item.title}</h3>
              <p className="mt-3 text-sm leading-relaxed text-[var(--ink-soft)]/75">{item.body}</p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
