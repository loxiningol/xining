import { NextResponse } from "next/server";
import { markets } from "@/lib/data";

export async function GET() {
  return NextResponse.json(markets);
}
