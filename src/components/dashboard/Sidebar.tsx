"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const links = [
  { href: "/dashboard", label: "总览" },
  { href: "/dashboard/portfolio", label: "持仓" },
  { href: "/dashboard/strategies", label: "策略" },
  { href: "/dashboard/backtest", label: "回测" },
  { href: "/dashboard/markets", label: "行情" },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="flex w-full flex-col border-b border-[var(--line)] bg-[var(--ink)] text-white md:min-h-screen md:w-56 md:border-b-0 md:border-r md:border-white/10">
      <div className="flex items-center justify-between px-5 py-5 md:block">
        <Link href="/" className="font-display text-lg tracking-[0.18em]">
          XINING
        </Link>
        <p className="hidden text-xs text-white/40 md:mt-2 md:block">量化交易控制台</p>
      </div>
      <nav className="flex gap-1 overflow-x-auto px-3 pb-3 md:flex-col md:px-3 md:pb-6">
        {links.map((link) => {
          const active =
            link.href === "/dashboard"
              ? pathname === "/dashboard"
              : pathname.startsWith(link.href);
          return (
            <Link
              key={link.href}
              href={link.href}
              className={cn(
                "whitespace-nowrap rounded-sm px-3 py-2 text-sm transition",
                active
                  ? "bg-white/10 text-white"
                  : "text-white/55 hover:bg-white/5 hover:text-white",
              )}
            >
              {link.label}
            </Link>
          );
        })}
      </nav>
    </aside>
  );
}
