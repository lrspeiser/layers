import type { Metadata } from "next";
import Link from "next/link";
import { DifferenceIndex, type AnomalyRow, type GateRow, type OperatorCard } from "@/components/DifferenceIndex";
import reconciliation from "@/public/data/layers/selected-regions/rubin-reference-reconciliation.json";
import recovery from "@/public/data/layers/selected-regions/region-diffuse-recovery.json";
import bandpass from "@/public/data/layers/selected-regions/bandpass-transfer.json";
import anomalies from "@/public/data/layers/selected-regions/region-anomalies.json";
import gaia from "@/public/data/layers/gaia-crossmatch/comparison-50.json";
import gas from "@/public/data/layers/hi-gas/comparison.json";

export const metadata: Metadata = {
  title: "Cross-survey differences",
  description:
    "Every comparison operator, the gates each product has cleared, and the residuals no known effect explains.",
};

function fixed(value: unknown, digits: number, fallback = "—") {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : fallback;
}

export default function DifferencesPage() {
  // Gates cleared come from the reconciliation stage. Injection/recovery and the
  // covariance measurement are region-level results from a separate stage, so
  // they are folded in here rather than being invisible.
  const recoveredRegions = new Set((recovery.regions ?? []).map((item: { regionId: string }) => item.regionId));
  const gates: GateRow[] = (reconciliation.regions ?? []).map((region: { regionId: string; clearedBlockers?: string[] }) => {
    const cleared = [...(region.clearedBlockers ?? [])];
    if (recoveredRegions.has(region.regionId)) {
      cleared.push("injection/recovery QA", "resampling covariance");
    }
    return { regionId: region.regionId, cleared };
  });

  const bandpassWithin = bandpass.counts?.withinTolerance ?? 0;
  const bandpassMeasured = bandpass.counts?.measured ?? 0;
  const universality = Object.values(bandpass.universality ?? {}) as Array<{
    consistentWithOneConstant?: boolean;
    reducedChiSquare?: number;
  }>;
  const anyConsistent = universality.some((item) => item.consistentWithOneConstant);

  const operators: OperatorCard[] = [
    {
      id: "optical",
      label: "Optical difference",
      kind: "same band",
      headline: `${reconciliation.counts?.reconciled ?? 0} regions`,
      detail: `Rubin against Legacy or Pan-STARRS on a common grid. ${reconciliation.counts?.psfMatched ?? 0} PSF-matched, ${reconciliation.counts?.skyMatched ?? 0} background-matched, ${reconciliation.counts?.astrometryPassed ?? 0} inside the 0.30″ astrometry threshold.`,
      state: "measured",
    },
    {
      id: "bandpass",
      label: "Bandpass transfer",
      kind: "filter transfer",
      headline: anyConsistent ? "consistent" : "ruled out",
      detail: `A Rubin colour term is significant in most fields and ${bandpassWithin} of ${bandpassMeasured} land inside the 0.08 mag tolerance, but the fitted term is not the same constant from field to field, so a single linear transfer does not describe it.`,
      state: "partial",
    },
    {
      id: "limits",
      label: "Detection limits",
      kind: "injection recovery",
      headline: `${recovery.counts?.measured ?? 0} regions`,
      detail: `Limiting surface brightness measured by injecting and recovering real sources. The empirical noise exceeds the propagated per-pixel uncertainty by a median factor of ${fixed(recovery.resamplingCovariance?.medianRubinEmpiricalToFormalNoiseRatio, 1)}.`,
      state: "measured",
    },
    {
      id: "gaia",
      label: "Gaia cross-match",
      kind: "catalogue match",
      headline: `${fixed(gaia.astrometry?.medianP95AtFittedEpochArcsec, 3)}″`,
      detail: `Rubin publishes no usable coadd epoch, so it is fitted from Gaia proper motions: ${fixed(gaia.epochConsistency?.medianFittedJyear, 2)} with ${fixed(gaia.epochConsistency?.scatterYears, 2)} yr scatter across ${gaia.counts?.measured ?? 0} fields. Applying it takes astrometry from ${fixed(gaia.astrometry?.medianP95WithoutPropagationArcsec, 3)}″ to this.`,
      state: "measured",
    },
    {
      id: "gas",
      label: "Neutral gas",
      kind: "scaling relation",
      headline: `${gas.counts?.usable ?? 0} detections`,
      detail: `H I mass and line width against optical light across ${gas.counts?.distinctTracts ?? 0} tracts. Not gated by the bandpass transfer, because this is a scaling-relation residual rather than a photometric difference.`,
      state: "partial",
    },
  ];

  const rows: AnomalyRow[] = (anomalies.topCandidates ?? []) as AnomalyRow[];

  return (
    <main id="top">
      <header className="layers-header">
        <Link className="layers-brand" href="/">
          <span className="brand-glyph">
            <i />
            <b />
          </span>
          <strong>Layers</strong>
          <small>science comparison workspace</small>
        </Link>
        <nav>
          <Link href="/">Full footprint</Link>
          <Link href="/pilots">Live multi-survey pilots</Link>
          <Link href="/prototype">Aligned optical prototype</Link>
        </nav>
        <span className="release-chip">DIFFERENCES</span>
      </header>
      <DifferenceIndex
        operators={operators}
        gates={gates}
        anomalies={rows}
        anomalyContext={{
          scanned: anomalies.counts?.regionsScanned ?? 0,
          skipped: anomalies.counts?.regionsSkipped ?? 0,
          raw: anomalies.counts?.totalCandidates ?? 0,
          surviving: anomalies.counts?.withoutBoringExplanation ?? 0,
        }}
      />
    </main>
  );
}
