import { NextResponse } from "next/server";
import { getRubinWorkerStatus } from "@/lib/rubin-on-demand";

export const runtime = "nodejs";

export async function GET() {
  const status = await getRubinWorkerStatus();
  return NextResponse.json(status ?? { status: "unavailable" }, {
    status: status ? 200 : 503,
    headers: { "Cache-Control": "public, max-age=30, stale-while-revalidate=60" },
  });
}
