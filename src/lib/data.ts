import type {
  BacktestResult,
  EquityPoint,
  MarketTicker,
  PortfolioSummary,
  Strategy,
  Trade,
} from "./types";

function buildEquityCurve(days = 90, start = 100000): EquityPoint[] {
  const points: EquityPoint[] = [];
  let equity = start;
  let benchmark = start;

  for (let i = days; i >= 0; i -= 1) {
    const date = new Date();
    date.setDate(date.getDate() - i);
    const drift = 0.0012 + Math.sin(i / 9) * 0.004;
    const noise = (Math.sin(i * 1.7) + Math.cos(i * 0.6)) * 0.003;
    equity *= 1 + drift + noise;
    benchmark *= 1 + 0.00055 + Math.sin(i / 14) * 0.0015;
    points.push({
      date: date.toISOString().slice(0, 10),
      equity: Math.round(equity * 100) / 100,
      benchmark: Math.round(benchmark * 100) / 100,
    });
  }

  return points;
}

export const strategies: Strategy[] = [
  {
    id: "str-momentum-btc",
    name: "BTC 动量突破",
    description: "基于多周期动量与波动率过滤的 BTCUSDT 趋势跟踪策略。",
    status: "running",
    pair: "BTCUSDT",
    timeframe: "15m",
    sharpe: 1.86,
    maxDrawdown: -8.4,
    totalReturn: 42.7,
    winRate: 57.2,
    allocated: 35000,
    updatedAt: "2026-08-03T18:20:00Z",
  },
  {
    id: "str-mean-eth",
    name: "ETH 均值回归",
    description: "在高波动区间捕捉 ETH 短期偏离后的均值回归机会。",
    status: "running",
    pair: "ETHUSDT",
    timeframe: "5m",
    sharpe: 1.42,
    maxDrawdown: -6.1,
    totalReturn: 28.3,
    winRate: 61.8,
    allocated: 22000,
    updatedAt: "2026-08-03T17:05:00Z",
  },
  {
    id: "str-funding-arb",
    name: "资金费率套利",
    description: "现货与永续对冲，捕获资金费率与基差收益。",
    status: "paused",
    pair: "BTCUSDT",
    timeframe: "1h",
    sharpe: 2.11,
    maxDrawdown: -3.2,
    totalReturn: 18.9,
    winRate: 72.4,
    allocated: 18000,
    updatedAt: "2026-08-02T09:40:00Z",
  },
  {
    id: "str-alt-rotation",
    name: "山寨轮动因子",
    description: "流动性与动量因子驱动的山寨币篮子轮动。",
    status: "backtest",
    pair: "MULTI",
    timeframe: "4h",
    sharpe: 1.19,
    maxDrawdown: -14.6,
    totalReturn: 63.5,
    winRate: 49.1,
    allocated: 0,
    updatedAt: "2026-08-01T12:10:00Z",
  },
  {
    id: "str-grid-sol",
    name: "SOL 动态网格",
    description: "根据 ATR 自适应调整网格间距的 SOL 做市网格。",
    status: "draft",
    pair: "SOLUSDT",
    timeframe: "1m",
    sharpe: 0.94,
    maxDrawdown: -9.8,
    totalReturn: 15.2,
    winRate: 68.5,
    allocated: 0,
    updatedAt: "2026-07-30T21:00:00Z",
  },
];

const equityCurve = buildEquityCurve();

export const portfolio: PortfolioSummary = {
  totalEquity: equityCurve[equityCurve.length - 1]?.equity ?? 124580,
  availableBalance: 41250.42,
  unrealizedPnl: 1842.66,
  todayPnl: 1264.18,
  todayPnlPct: 1.02,
  equityCurve,
  positions: [
    {
      id: "pos-1",
      symbol: "BTCUSDT",
      side: "long",
      size: 0.85,
      entryPrice: 68420,
      markPrice: 69210,
      pnl: 671.5,
      pnlPct: 1.15,
      leverage: 3,
    },
    {
      id: "pos-2",
      symbol: "ETHUSDT",
      side: "long",
      size: 12.4,
      entryPrice: 3482,
      markPrice: 3528,
      pnl: 570.4,
      pnlPct: 1.32,
      leverage: 2,
    },
    {
      id: "pos-3",
      symbol: "SOLUSDT",
      side: "short",
      size: 180,
      entryPrice: 178.4,
      markPrice: 176.9,
      pnl: 270,
      pnlPct: 0.84,
      leverage: 4,
    },
    {
      id: "pos-4",
      symbol: "BNBUSDT",
      side: "long",
      size: 28,
      entryPrice: 612.5,
      markPrice: 624.1,
      pnl: 324.8,
      pnlPct: 1.89,
      leverage: 2,
    },
  ],
  recentTrades: [
    {
      id: "tr-1",
      strategyId: "str-momentum-btc",
      symbol: "BTCUSDT",
      side: "long",
      price: 69180,
      quantity: 0.12,
      pnl: 86.4,
      time: "2026-08-03T22:14:00Z",
    },
    {
      id: "tr-2",
      strategyId: "str-mean-eth",
      symbol: "ETHUSDT",
      side: "short",
      price: 3542,
      quantity: 2.5,
      pnl: -38.2,
      time: "2026-08-03T21:02:00Z",
    },
    {
      id: "tr-3",
      strategyId: "str-momentum-btc",
      symbol: "BTCUSDT",
      side: "long",
      price: 68890,
      quantity: 0.2,
      pnl: 142.6,
      time: "2026-08-03T19:41:00Z",
    },
    {
      id: "tr-4",
      strategyId: "str-mean-eth",
      symbol: "ETHUSDT",
      side: "long",
      price: 3498,
      quantity: 3.1,
      pnl: 94.8,
      time: "2026-08-03T16:28:00Z",
    },
    {
      id: "tr-5",
      strategyId: "str-funding-arb",
      symbol: "BTCUSDT",
      side: "short",
      price: 69010,
      quantity: 0.5,
      pnl: 52.1,
      time: "2026-08-03T11:05:00Z",
    },
  ],
};

export const markets: MarketTicker[] = [
  {
    symbol: "BTCUSDT",
    price: 69210,
    change24h: 1.84,
    volume24h: 28400000000,
    fundingRate: 0.0102,
  },
  {
    symbol: "ETHUSDT",
    price: 3528,
    change24h: 2.31,
    volume24h: 14200000000,
    fundingRate: 0.0086,
  },
  {
    symbol: "SOLUSDT",
    price: 176.9,
    change24h: -0.92,
    volume24h: 4100000000,
    fundingRate: -0.0041,
  },
  {
    symbol: "BNBUSDT",
    price: 624.1,
    change24h: 1.12,
    volume24h: 1800000000,
    fundingRate: 0.0055,
  },
  {
    symbol: "XRPUSDT",
    price: 2.84,
    change24h: 0.46,
    volume24h: 3200000000,
    fundingRate: 0.0028,
  },
  {
    symbol: "DOGEUSDT",
    price: 0.1824,
    change24h: -1.55,
    volume24h: 1600000000,
    fundingRate: -0.0019,
  },
];

export const backtests: BacktestResult[] = [
  {
    id: "bt-1",
    strategyName: "BTC 动量突破",
    pair: "BTCUSDT",
    period: "2025-01-01 → 2026-08-01",
    totalReturn: 86.4,
    sharpe: 1.92,
    maxDrawdown: -11.3,
    winRate: 56.8,
    trades: 428,
    equity: buildEquityCurve(180, 100000).map((p) => ({
      ...p,
      equity: p.equity * 1.08,
    })),
  },
  {
    id: "bt-2",
    strategyName: "ETH 均值回归",
    pair: "ETHUSDT",
    period: "2025-03-01 → 2026-08-01",
    totalReturn: 54.2,
    sharpe: 1.48,
    maxDrawdown: -9.7,
    winRate: 62.1,
    trades: 812,
    equity: buildEquityCurve(160, 100000),
  },
  {
    id: "bt-3",
    strategyName: "山寨轮动因子",
    pair: "MULTI",
    period: "2024-06-01 → 2026-08-01",
    totalReturn: 128.6,
    sharpe: 1.21,
    maxDrawdown: -22.4,
    winRate: 48.3,
    trades: 266,
    equity: buildEquityCurve(200, 80000).map((p, i) => ({
      ...p,
      equity: p.equity * (1 + Math.sin(i / 20) * 0.05),
    })),
  },
];

export function getStrategy(id: string) {
  return strategies.find((s) => s.id === id);
}

export function getTradesByStrategy(strategyId: string): Trade[] {
  return portfolio.recentTrades.filter((t) => t.strategyId === strategyId);
}
