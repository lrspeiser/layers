import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { TractLayerComposite, type CompositeLayer } from "@/components/TractLayerComposite";
import productIndexData from "@/public/data/layers/tract-product-index.json";

export const metadata: Metadata = {
  title: "Layer overlay",
  description: "Every measured survey layer placed on the same sky as the Rubin image.",
};

// This lives on its own route rather than inside /tract/[tract]. That page is a
// dynamic route already importing about ten large JSON files, and adding the
// overlay to it made every tract page fail to render in the worker build. A
// separate, small route gets the same result without touching a page that works.

type PlacedProduct = {
  tract: number;
  regionId: string;
  surveyId: string;
  surveyName?: string;
  family?: string;
  bandOrObservable?: string;
  referenceBand?: string;
  rubinBand?: string;
  referenceImage?: string;
  rubinImage?: string;
  viewerReady?: boolean;
  skyPlacement?: {
    leftPercent: number;
    topPercent: number;
    widthPercent: number;
    heightPercent: number;
    requiresRepositioning: boolean;
    widthArcmin: number;
    heightArcmin: number;
    centreOffsetArcmin: number;
  };
};

export default async function OverlayPage({ params }: { params: Promise<{ tract: string }> }) {
  const { tract: raw } = await params;
  const tract = Number(raw);
  if (!Number.isInteger(tract)) notFound();

  const products = (productIndexData.products as unknown as PlacedProduct[]).filter(
    (product) => product.tract === tract,
  );
  if (!products.length) notFound();

  const base = products.find((product) => product.viewerReady && product.rubinImage);
  const placed = products.filter(
    (product) => product.skyPlacement && product.referenceImage && product.surveyId !== "legacy-surveys-dr10",
  );
  const baseFrame = products.find((product) => product.surveyId === "legacy-surveys-dr10")?.skyPlacement;

  const layers: CompositeLayer[] = placed.map((product, index) => ({
    id: `${product.surveyId}-${index}`,
    surveyName: product.surveyName ?? product.surveyId,
    family: product.family ?? "",
    band: String(product.bandOrObservable ?? product.referenceBand ?? ""),
    image: product.referenceImage as string,
    tint: String((index * 67) % 360),
    placement: {
      leftPercent: product.skyPlacement!.leftPercent,
      topPercent: product.skyPlacement!.topPercent,
      widthPercent: product.skyPlacement!.widthPercent,
      heightPercent: product.skyPlacement!.heightPercent,
      requiresRepositioning: product.skyPlacement!.requiresRepositioning,
      note: "",
    },
    geometry: {
      widthArcmin: product.skyPlacement!.widthArcmin,
      heightArcmin: product.skyPlacement!.heightArcmin,
      centreOffsetArcmin: product.skyPlacement!.centreOffsetArcmin,
    },
  }));

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
          <Link href={`/tract/${tract}`}>Tract evidence</Link>
          <Link href="/differences">Cross-survey differences</Link>
          <Link href="/">Full footprint</Link>
        </nav>
        <span className="release-chip">TRACT {tract} · OVERLAY</span>
      </header>

      {base && layers.length ? (
        <section style={{ padding: "clamp(1.5rem,4vw,3rem)" }}>
          <TractLayerComposite
            tract={tract}
            baseImage={base.rubinImage as string}
            baseLabel={`Rubin DP2 · ${base.rubinBand ?? "r"}`}
            baseFrameArcmin={baseFrame?.widthArcmin ?? 3.41}
            layers={layers}
          />
        </section>
      ) : (
        <section style={{ padding: "clamp(1.5rem,4vw,3rem)", color: "#a7aaa4" }}>
          <p>
            No overlay is available for tract {tract}: it needs a validated Rubin image and at least one
            other layer whose sky placement has been measured.
          </p>
          <p>
            <Link href={`/tract/${tract}`}>Open the tract evidence workspace →</Link>
          </p>
        </section>
      )}
    </main>
  );
}
