import type { Metadata } from "next";
import Link from "next/link";
import { DifferenceIndex, type AnomalyRow, type GateRow, type OperatorCard } from "@/components/DifferenceIndex";
import summary from "@/public/data/layers/site-summary.json";

// The page never imports an analysis manifest. The 190-region reconciliation is
// 1.4 MB on its own, and a 525 KB module already broke every tract page earlier
// by pushing a route's worker chunk past what the runtime would load.
// build_site_summary.py reduces them all to the fields drawn here.

export const metadata: Metadata = {
  title: "Cross-survey differences",
  description:
    "Every comparison operator, the gates each product has cleared, and the residuals no known effect explains.",
};

function fixed(value: unknown, digits: number, fallback = "—") {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : fallback;
}

export default function DifferencesPage() {
  const gates: GateRow[] = (summary.gates ?? []).map((row: { regionId: string; cleared: string[] }) => ({
    regionId: row.regionId,
    cleared: row.cleared ?? [],
  }));

  const universality = Object.values(summary.bandpass?.universality ?? {}) as Array<{
    consistentWithOneConstant?: boolean;
  }>;
  const anyConsistent = universality.some((item) => item.consistentWithOneConstant);
  const bandpassMeasured = summary.bandpass?.measured ?? 0;

  const operators: OperatorCard[] = [
    {
      id: "optical",
      label: "Optical difference",
      kind: "same band",
      headline: `${(summary.reconciliation?.reconciled ?? 0) + (summary.des?.reconciled ?? 0) + (summary.ps1?.reconciled ?? 0)} pairs`,
      detail: `Three independently calibrated references on a common grid with Rubin: Legacy ${summary.reconciliation?.reconciled ?? 0}, DES ${summary.des?.reconciled ?? 0}, Pan-STARRS ${summary.ps1?.reconciled ?? 0} reconciled. Of the Legacy set, ${summary.reconciliation?.psfMatched ?? 0} are PSF-matched, ${summary.reconciliation?.skyMatched ?? 0} background-matched, ${summary.reconciliation?.astrometryPassed ?? 0} inside the 0.30″ astrometry threshold.`,
      state: "measured",
    },
    {
      id: "bandpass",
      label: "Bandpass transfer",
      kind: "filter transfer",
      headline: bandpassMeasured ? (anyConsistent ? "consistent" : "ruled out") : "pending",
      detail: bandpassMeasured
        ? `A Rubin colour term is significant in most fields and ${summary.bandpass?.withinTolerance ?? 0} of ${bandpassMeasured} land inside the 0.08 mag tolerance, but the fitted term is not the same constant from field to field, so a single linear transfer does not describe it.`
        : "Waiting on the second Rubin band for the larger region set. A colour term cannot be fitted without a Rubin colour.",
      state: bandpassMeasured ? "partial" : "none",
    },
    {
      id: "limits",
      label: "Detection limits",
      kind: "injection recovery",
      headline: `${summary.recovery?.measured ?? 0} regions`,
      detail: `Limiting surface brightness measured by injecting and recovering real sources. The empirical noise exceeds the propagated per-pixel uncertainty by a median factor of ${fixed(summary.recovery?.medianEmpiricalToFormalNoiseRatio, 1)}.`,
      state: "measured",
    },
    {
      id: "gaia",
      label: "Gaia cross-match",
      kind: "catalogue match",
      headline: `${fixed(summary.gaia?.astrometry?.medianP95AtFittedEpochArcsec, 3)}″`,
      detail: `Rubin publishes no usable coadd epoch, so it is fitted from Gaia proper motions: ${fixed(summary.gaia?.epoch?.medianFittedJyear, 2)} with ${fixed(summary.gaia?.epoch?.scatterYears, 2)} yr scatter across ${summary.gaia?.measured ?? 0} fields. Applying it takes astrometry from ${fixed(summary.gaia?.astrometry?.medianP95WithoutPropagationArcsec, 3)}″ to this.`,
      state: "measured",
    },
    {
      id: "gas",
      label: "Neutral gas",
      kind: "scaling relation",
      headline: `${summary.gas?.usable ?? 0} detections`,
      detail: `H I mass and line width against optical light. ${summary.gas?.tullyFisher?.withUsableInclination ?? 0} have a usable inclination, and the residual tracks rotation velocity rather than mass, so no object is called a departure.`,
      state: "partial",
    },
    {
      id: "sed",
      label: "SED consistency",
      kind: "different band",
      headline: `${summary.sed?.sedSources ?? 0} sources`,
      detail: `2MASS and AllWISE photometry predict the Rubin flux; departures are ranked against the observed scatter of ${fixed(summary.sed?.colourRelation?.residualScatterDex, 3)} dex about the fitted infrared-colour relation.`,
      state: "partial",
    },
    {
      id: "xray",
      label: "X-ray counterparts",
      kind: "association",
      headline: `${summary.xray?.xraySourcesInsideRubinPixels ?? 0} sources`,
      detail: `eRASS1 detections inside Rubin pixels: ${summary.xray?.withOpticalCounterpart ?? 0} with an optical counterpart, ${summary.xray?.withoutOpticalCounterpart ?? 0} without, to a stated depth. Only ${summary.xray?.regionsWithAnyCataloguedSource ?? 0} of ${summary.xray?.regionsQueried ?? 0} regions hold a catalogued X-ray source at all.`,
      state: "measured",
    },
    {
      id: "radio",
      label: "Radio counterparts",
      kind: "association",
      headline: `${summary.radio?.radioSourcesInsideRubinPixels ?? 0} sources`,
      detail: `VLASS 3 GHz detections inside Rubin pixels across ${summary.radio?.fieldsSearched ?? 0} fields: ${summary.radio?.withOpticalCounterpart ?? 0} with optical light at the position, ${summary.radio?.withoutOpticalCounterpart ?? 0} without. Radio and optical trace different emission, so a bare radio source is an association result, not a photometric difference.`,
      state: "measured",
    },
    {
      id: "highres",
      label: "Independent-resolution check",
      kind: "verification",
      headline: `${summary.euclid?.verdictsDelivered ?? 0} verdicts`,
      detail: `Euclid Q1 VIS and NISP, at 0.10″ pixels. Of ${summary.euclid?.candidates ?? 0} candidates, ${summary.euclid?.coveredByIndependentEuclid ?? 0} sit inside a real Euclid footprint polygon and ${summary.euclid?.footprintContainsButNoPixels ?? 0} of those returns an all-zero cutout — the polygon contains the position, the tile has no pixels there. ${
        (summary.euclid?.verdicts ?? []).length > 0
          ? (summary.euclid.verdicts as Array<{ reading: string }>)[0].reading
          : ""
      }`,
      state: (summary.euclid?.verdictsDelivered ?? 0) > 0 ? "partial" : "none",
    },
    {
      id: "second-reference",
      label: "Independent references",
      kind: "attribution",
      headline: `${summary.crossCheck?.referencesCompared ?? 0} verified chains`,
      detail: `Rubin is the only term every optical pairing shares, which is what turns a measured difference into an answer about whose it is. ${summary.crossCheck?.sharedRegions ?? 0} regions are measured against more than one reference. ${
        (summary.crossCheck?.unverifiedChainFlags ?? []).length > 0
          ? `Pan-STARRS is acquired and reconciled but its absolute flux chain has never been checked against the survey's own catalogue, so it is excluded from the zeropoint test and carries its own flag below.`
          : `Every reference here has a verified absolute flux chain.`
      }`,
      state: "measured",
    },
  ];

  const rows: AnomalyRow[] = (summary.topAnomalies ?? []) as unknown as AnomalyRow[];
  const attribution = (summary.crossCheck?.findings ?? []) as Array<{
    question: string;
    verdict: string;
    basis: string;
  }>;

  // The curve of growth answers the one question the two-reference test could
  // not: whether the deficit is aperture or calibration. It reads as a fourth
  // attribution because that is what it is.
  const curvePairings = Object.entries(summary.curveOfGrowth?.pairings ?? {}) as Array<
    [string, { fields: number; sources: number; gain: number; interval: number[]; verdict: string }]
  >;
  const dissenting = (summary.curveOfGrowth?.dissentingPairings ?? []) as string[];
  const flatPairings = (summary.curveOfGrowth?.flatPairings ?? []) as string[];
  if (curvePairings.length > 0) {
    attribution.push({
      question: "Is the Rubin flux deficit an aperture effect or a zeropoint?",
      // Never collapse this to the first pairing's verdict. With three pairings
      // that read the first one alone and it would have reported a clean
      // "zeropoint-like" while one pairing was trending the other way.
      verdict:
        dissenting.length === 0
          ? "a zeropoint-like constant, not an aperture effect"
          : `zeropoint-like against ${flatPairings
              .map((name) => name.replace("rubin-vs-", ""))
              .join(" and ")}; ${dissenting
              .map((name) => name.replace("rubin-vs-", ""))
              .join(" and ")} dissents`,
      basis: `${curvePairings
        .map(
          ([name, value]) =>
            `${name.replace("rubin-vs-", "")}: gain ${value.gain.toFixed(4)} [${value.interval[0].toFixed(
              3,
            )}, ${value.interval[1].toFixed(3)}] over ${value.fields} fields and ${value.sources} isolated sources`,
        )
        .join("; ")}. The ratio is measured at seven radii from 1.0″ to 5.0″ on the PSF-matched planes. An aperture effect would climb toward 1 as the aperture grows; a constant calibration factor would not care. Only sources with no detected neighbour within three times the largest aperture are used, because blending would imitate the climb.${
        summary.curveOfGrowth?.attribution ? ` ${summary.curveOfGrowth.attribution}` : ""
      }`,
    });
  }

  // A reference whose absolute flux chain was never checked can be wrong by a
  // constant, which is the whole quantity a zeropoint test reads. Showing its
  // scale beside two verified ones without saying so would invite the reader to
  // conclude the surveys disagree.
  const chainFlags = (summary.crossCheck?.unverifiedChainFlags ?? []) as Array<{
    reference: string;
    medianScale: number;
    departureMag: number;
    reading: string;
    toResolve: string;
  }>;

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
          <Link href="/data">Download the catalogue</Link>
          <Link href="/explorer">Difference explorer</Link>
          <Link href="/goals">Goals</Link>
          <Link href="/pilots">Live multi-survey pilots</Link>
          <Link href="/prototype">Aligned optical prototype</Link>
        </nav>
        <span className="release-chip">DIFFERENCES</span>
      </header>
      <DifferenceIndex
        operators={operators}
        chainFlags={chainFlags}
        gates={gates}
        anomalies={rows}
        attribution={attribution}
        register={{
          candidates: summary.register?.candidates ?? 0,
          evaluated: summary.register?.comparisonsEvaluated ?? 0,
          confirmed: summary.register?.flaggedByMoreThanOneOperator ?? 0,
        }}
        anomalyContext={{
          scanned: summary.anomalies?.regionsScanned ?? 0,
          skipped: summary.anomalies?.regionsSkipped ?? 0,
          raw: summary.anomalies?.totalCandidates ?? 0,
          surviving: summary.anomalies?.withoutBoringExplanation ?? 0,
        }}
      />
    </main>
  );
}
