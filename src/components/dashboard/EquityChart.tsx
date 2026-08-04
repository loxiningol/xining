"use client";

import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { EquityPoint } from "@/lib/types";
import { formatUsd } from "@/lib/utils";

export function EquityChart({ data }: { data: EquityPoint[] }) {
  return (
    <div className="chart-shell h-72 w-full md:h-80">
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={data} margin={{ top: 10, right: 8, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#14a07a" stopOpacity={0.35} />
              <stop offset="100%" stopColor="#14a07a" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid stroke="rgba(15,36,32,0.08)" vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fill: "rgba(15,36,32,0.45)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            minTickGap={40}
          />
          <YAxis
            tick={{ fill: "rgba(15,36,32,0.45)", fontSize: 11 }}
            tickLine={false}
            axisLine={false}
            width={64}
            tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`}
          />
          <Tooltip
            contentStyle={{
              background: "#071512",
              border: "none",
              borderRadius: 4,
              color: "#fff",
              fontSize: 12,
            }}
            formatter={(value, name) => [
              formatUsd(Number(value)),
              name === "equity" ? "策略净值" : "基准",
            ]}
            labelFormatter={(label) => String(label)}
          />
          <Area
            type="monotone"
            dataKey="equity"
            stroke="#0f6b57"
            strokeWidth={2}
            fill="url(#equityFill)"
          />
          <Line
            type="monotone"
            dataKey="benchmark"
            stroke="rgba(15,36,32,0.35)"
            strokeWidth={1.5}
            dot={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}
