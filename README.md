# XINING · 加密货币量化交易平台

策略研发、回测验证与实盘控制台一体的量化交易网站 MVP。

## 功能

- **品牌官网**：产品介绍、策略样本与工作流程
- **交易控制台**：账户总览、净值曲线、持仓与成交
- **策略管理**：运行状态、绩效指标与资金分配
- **回测结果**：历史区间收益、夏普、回撤对比
- **市场行情**：价格、涨跌、成交额与资金费率
- **Mock API**：`/api/portfolio`、`/api/strategies`、`/api/markets`、`/api/backtests`

当前使用模拟数据，便于先完成产品与交互；后续可对接交易所 API 与自研交易引擎。

## 技术栈

- Next.js 16（App Router）
- TypeScript
- Tailwind CSS 4
- Recharts

## 本地运行

```bash
npm install
npm run dev
```

打开 [http://localhost:3000](http://localhost:3000)。

```bash
npm run build
npm start
```

## 目录结构

```
src/
  app/                 # 页面与 API 路由
  components/landing/  # 官网组件
  components/dashboard/# 控制台组件
  lib/                 # 类型、工具与模拟数据
```

## 下一步建议

1. 接入 Binance / OKX 行情与下单 API
2. 增加用户认证与 API Key 安全管理
3. 策略参数编辑与在线回测任务队列
4. WebSocket 实时净值与持仓推送
