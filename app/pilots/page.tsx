import type { Metadata } from "next";
import Link from "next/link";
import { MultisurveyPilotExplorer, type MultisurveyPilot, type PilotDataset } from "@/components/MultisurveyPilotExplorer";
import ugc00191 from "@/public/data/layers/multisurvey-pilots/ugc00191.json";
import ugc00634 from "@/public/data/layers/multisurvey-pilots/ugc00634.json";
import ugc00891 from "@/public/data/layers/multisurvey-pilots/ugc00891.json";
import commonGrid from "@/public/data/layers/multisurvey-pilots/rubin-lotss-common-grid-summary.json";

export const metadata: Metadata = { title: "Live multi-survey pilots", description: "Real Gaia, ZTF, eROSITA, VLASS, LoTSS, and Rubin evidence at the three initial Rubin fields." };

type RawDataset = Record<string, unknown> & { dataset: string; release: string; status: "available" | "none" | "error"; coverage: boolean; readiness: string; caveats: string[] };
type RawPilot = { field: { id: string; name: string; raDeg: number; decDeg: number }; datasets: RawDataset[] };

const labels: Record<string, string> = { "gaia-dr3": "Gaia DR3", "ztf-dr24": "ZTF time series", "erosita-erass1": "eROSITA eRASS1", vlass: "VLASS radio", lotss: "LoTSS DR3 radio" };

function count(dataset: RawDataset, key: string) { return Number(dataset[key] ?? 0).toLocaleString(); }
function summarize(dataset: RawDataset) {
  if (dataset.status === "none") return dataset.dataset === "erosita-erass1" ? "The official service reports this position is outside the public eROSITA-DE DR1 sky." : "No public records were returned for this bounded field query.";
  if (dataset.dataset === "gaia-dr3") return `${count(dataset, "recordCount")} sources in the cone; ${count(dataset, "sourcesWithProperMotion")} have proper-motion measurements for foreground rejection.`;
  if (dataset.dataset === "ztf-dr24") return `${count(dataset, "lightCurveMeasurementCount")} measurements across ${count(dataset, "lightCurveObjectCount")} bounded objects; ${count(dataset, "referenceImageRecordCount")} reference-image records.`;
  if (dataset.dataset === "vlass") return `${count(dataset, "recordCount")} official CADC ObsCore records establish archive coverage; image pixels have not yet been promoted.`;
  if (dataset.dataset === "lotss") return `${count(dataset, "recordCount")} catalog sources plus an authentic 200 × 200 FITS radio cutout with celestial WCS.`;
  return `${count(dataset, "recordCount")} records returned.`;
}

function adapt(raw: RawPilot): MultisurveyPilot {
  const aligned = commonGrid.fields.find((field) => field.fieldId === raw.field.id);
  if (!aligned) throw new Error(`Missing Rubin/LoTSS common-grid manifest for ${raw.field.id}`);
  return {
    ...raw.field,
    alignedRubinPreview: aligned.previews.rubinAligned,
    alignedLotssPreview: aligned.previews.lotssNativeCommonGrid,
    coveragePreview: aligned.previews.commonCoverage,
    overlayPreview: aligned.previews.positionOverlay,
    commonCoverageFraction: aligned.commonCoverageFraction,
    datasets: raw.datasets.map((dataset): PilotDataset => ({ dataset: dataset.dataset, label: labels[dataset.dataset] ?? dataset.dataset, status: dataset.status, coverage: dataset.coverage, release: dataset.release, summary: summarize(dataset), readiness: dataset.readiness, caveats: dataset.caveats ?? [] })),
  };
}

export default async function PilotsPage({ searchParams }: { searchParams: Promise<{ field?: string }> }) {
  const { field } = await searchParams;
  const pilots = [ugc00191, ugc00634, ugc00891].map((item) => adapt(item as unknown as RawPilot));
  return <main id="top"><header className="layers-header"><Link className="layers-brand" href="/"><span className="brand-glyph"><i /><b /></span><strong>Layers</strong><small>science comparison workspace</small></Link><nav><Link href="/">Full footprint</Link><Link href="/prototype">Aligned optical prototype</Link><Link href="/workspace">SPARC workspace</Link></nav><span className="release-chip">LIVE ARCHIVE PILOTS</span></header><MultisurveyPilotExplorer pilots={pilots} initialPilotId={field} /></main>;
}
