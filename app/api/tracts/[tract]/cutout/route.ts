import { NextResponse } from "next/server";
import { getRubinCutoutJob, getTractCenter, queueRubinCutout } from "@/lib/rubin-on-demand";

export const runtime = "nodejs";
export const maxDuration = 30;

type RouteContext = { params: Promise<{ tract: string }> };

function parseTract(value: string) {
  return /^\d+$/.test(value) ? Number(value) : Number.NaN;
}

export async function GET(_request: Request, { params }: RouteContext) {
  const tract = parseTract((await params).tract);
  if (!Number.isInteger(tract) || !getTractCenter(tract)) {
    return NextResponse.json({ error: "Unknown Rubin tract" }, { status: 404 });
  }
  const job = await getRubinCutoutJob(tract);
  return NextResponse.json(job ?? { tract, status: "not-requested" }, {
    headers: { "Cache-Control": "private, no-store" },
  });
}

export async function POST(_request: Request, { params }: RouteContext) {
  const tract = parseTract((await params).tract);
  if (!Number.isInteger(tract) || !getTractCenter(tract)) {
    return NextResponse.json({ error: "Unknown Rubin tract" }, { status: 404 });
  }
  try {
    const job = await queueRubinCutout(tract);
    return NextResponse.json(job, {
      status: job.status === "complete" ? 200 : 202,
      headers: { "Cache-Control": "private, no-store" },
    });
  } catch (error) {
    return NextResponse.json({ error: error instanceof Error ? error.message : "Cutout request failed" }, { status: 503 });
  }
}
