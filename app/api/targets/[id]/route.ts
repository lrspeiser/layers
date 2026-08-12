import { NextResponse } from "next/server";
import catalogData from "@/public/data/layers-catalog.json";

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const target = catalogData.targets.find((item) => item.id === id);
  if (!target) return NextResponse.json({ error: "Target not found" }, { status: 404 });
  return NextResponse.json(
    { schemaVersion: catalogData.schemaVersion, release: catalogData.release, target },
    { headers: { "Cache-Control": "public, max-age=300, stale-while-revalidate=3600" } },
  );
}
