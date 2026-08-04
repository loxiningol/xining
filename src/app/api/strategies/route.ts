import { NextResponse } from "next/server";
import { strategies } from "@/lib/data";

export async function GET() {
  return NextResponse.json(strategies);
}
