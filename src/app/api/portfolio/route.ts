import { NextResponse } from "next/server";
import { portfolio } from "@/lib/data";

export async function GET() {
  return NextResponse.json(portfolio);
}
