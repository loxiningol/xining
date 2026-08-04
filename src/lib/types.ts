export type StrategyStatus = "running" | "paused" | "backtest" | "draft";

export type MarketSide = "long" | "short";

export interface EquityPoint {
  date: string;
  equity: number;
  benchmark: number;
}

export interface Position {
  id: string;
  symbol: string;
  side: MarketSide;
  size: number;
  entryPrice: number;
  markPrice: number;
  pnl: number;
  pnlPct: number;
  leverage: number;
}

export interface Strategy {
  id: string;
  name: string;
  description: string;
  status: StrategyStatus;
  pair: string;
  timeframe: string;
  sharpe: number;
  maxDrawdown: number;
  totalReturn: number;
  winRate: number;
  allocated: number;
  updatedAt: string;
}

export interface Trade {
  id: string;
  strategyId: string;
  symbol: string;
  side: MarketSide;
  price: number;
  quantity: number;
  pnl: number;
  time: string;
}

export interface MarketTicker {
  symbol: string;
  price: number;
  change24h: number;
  volume24h: number;
  fundingRate: number;
}

export interface BacktestResult {
  id: string;
  strategyName: string;
  pair: string;
  period: string;
  totalReturn: number;
  sharpe: number;
  maxDrawdown: number;
  winRate: number;
  trades: number;
  equity: EquityPoint[];
}

export interface PortfolioSummary {
  totalEquity: number;
  availableBalance: number;
  unrealizedPnl: number;
  todayPnl: number;
  todayPnlPct: number;
  equityCurve: EquityPoint[];
  positions: Position[];
  recentTrades: Trade[];
}
