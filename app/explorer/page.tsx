import type { Metadata } from "next";
import Link from "next/link";
import {
  DifferenceExplorer,
  type ConfirmedPosition,
  type DifferenceRegion,
  type RegisterCandidate,
} from "@/components/DifferenceExplorer";
import index from "@/public/data/layers/selected-regions/difference-index.json";
import agreement from "@/public/data/layers/selected-regions/difference-agreement-slim.json";
import placements from "@/public/data/layers/selected-regions/register-placements.json";

// The slim index only. The full difference-maps.json is 0.9 MB and the per-region
// peak lists are fetched by the client when a region is opened: a 525 KB module
// already broke every tract route once by pushing its worker chunk past what the
// runtime would load.

export const metadata: Metadata = {
  title: "Difference explorer",
  description:
    "Where Rubin and a reference survey disagree, drawn over the sky image and ranked by how much of the frame differs.",
};

export default function ExplorerPage() {
  const regions = (index.regions ?? []) as DifferenceRegion[];
  // Counts the confirmed panel quotes come from the agreement build, not the
  // per-pairing index, so they travel together.
  const counts = { ...index.counts, ...agreement.counts } as unknown as Record<string, number>;
  // TypeScript infers each entry's seenIn as its own literal shape, since the
  // references present differ per position, so the JSON needs one cast here.
  const confirmed = agreement.confirmed as unknown as ConfirmedPosition[];
  const byRegion = placements.byRegion as unknown as Record<string, RegisterCandidate[]>;

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
          <Link href="/differences">Operators</Link>
          <Link href="/coverage">Coverage</Link>
        </nav>
        <span className="release-chip">EXPLORER</span>
      </header>
      <DifferenceExplorer
        regions={regions}
        previewRoot={index.previewRoot}
        peakRoot={index.peakRoot}
        counts={counts}
        caveat={index.caveat}
        peakClassification={index.peakClassification}
        confirmed={confirmed}
        agreementCaveat={agreement.caveat}
        placements={byRegion}
        placementMeaning={placements.meaning}
      />
    </main>
  );
}
