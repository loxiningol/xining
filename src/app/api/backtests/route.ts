import { NextResponse } from "next/server";
import { backtests } from "@/lib/data";

export async function GET() {
  return NextResponse.json(backtests);
}
