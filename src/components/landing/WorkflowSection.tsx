const steps = [
  { step: "01", title: "接入行情", body: "订阅主流永续与现货行情，标准化 K 线、深度与资金费率。" },
  { step: "02", title: "构建策略", body: "用参数化模板快速组合信号，或导入自研逻辑进行沙盒验证。" },
  { step: "03", title: "回测评估", body: "在历史区间上压力测试，筛选夏普高、回撤可控的候选策略。" },
  { step: "04", title: "实盘托管", body: "一键部署到交易台，持续监控净值、持仓与成交归因。" },
];

export function WorkflowSection() {
  return (
    <section id="workflow" className="atmosphere-light py-24 md:py-32">
      <div className="mx-auto max-w-6xl px-5 md:px-8">
        <p className="text-sm tracking-[0.2em] text-[var(--teal)]">WORKFLOW</p>
        <h2 className="mt-3 font-display text-3xl text-[var(--ink)] md:text-5xl">四步进入量化闭环</h2>
        <p className="mt-4 max-w-2xl text-[var(--ink-soft)]/80">
          研究、验证、部署、复盘形成完整循环，减少从想法到成交的摩擦。
        </p>

        <ol className="mt-14 grid gap-8 md:grid-cols-2">
          {steps.map((item) => (
            <li key={item.step} className="flex gap-5">
              <span className="font-display text-3xl text-[var(--teal)]">{item.step}</span>
              <div>
                <h3 className="font-display text-xl text-[var(--ink)]">{item.title}</h3>
                <p className="mt-2 text-sm leading-relaxed text-[var(--ink-soft)]/75">{item.body}</p>
              </div>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
