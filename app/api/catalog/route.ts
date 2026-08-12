import { NextResponse } from "next/server";
import catalogData from "@/public/data/layers-catalog.json";

export function GET() {
  return NextResponse.json(catalogData, {
    headers: { "Cache-Control": "public, max-age=300, stale-while-revalidate=3600" },
  });
}
