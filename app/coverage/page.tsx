import type { Metadata } from "next";
import Link from "next/link";
import { CoverageExplorer } from "@/components/CoverageExplorer";
import {
  buildCoverageExplorerData,
  type CacheManifestFile,
  type ExternalOverlapFile,
  type RubinFootprintFile,
  type ResolvedProductFootprint,
  type SelectedRegionsFile,
} from "@/lib/coverage";
import type { SurveyRegistry } from "@/lib/survey-registry";
import footprintData from "@/public/data/coverage/rubin-dp2-footprint.json";
import overlapData from "@/public/data/coverage/external-overlaps.json";
import selectedRegionsData from "@/public/data/coverage/selected-regions.json";
import cacheManifestData from "@/public/data/coverage/acquisition-50-summary.json";
import rubinPixelsData from "@/public/data/coverage/rubin-pixels-50.json";
import productIndexData from "@/public/data/layers/tract-product-index.json";
import largeFootprintResolutionData from "@/public/data/coverage/large-footprint-resolution.json";
import conservativeSubsetData from "@/public/data/coverage/conservative-subset-evidence.json";
import hscKidsGapAuditData from "@/public/data/coverage/hsc-kids-gap-audit.json";
import hscPublicProductsData from "@/public/data/coverage/hsc-public-products.json";
import desiDr1ResolutionData from "@/public/data/coverage/desi-dr1-resolution.json";
import goalAuditData from "@/public/data/coverage/goal-audit.json";
import surveyRegistryData from "@/public/data/survey-registry.json";

export const metadata: Metadata = {
  title: "Full Rubin footprint",
  description: "Explore every live Rubin DP2 tract and its confirmed external-survey footprint intersections.",
};

export default function CoveragePage() {
  const kidsProduct = hscKidsGapAuditData.products.find((product) => product.surveyId === "kids-1000-lensing") as unknown as ({
    surveyId: string; status: string; release: string; coverageSemantics: string;
    releasedGoldSupport: { rubinOverlapTractCount: number; rubinOverlapTractIds: number[] };
  } | undefined);
  const kidsResolvedProduct = kidsProduct ? {
    surveyId: kidsProduct.surveyId,
    status: kidsProduct.status,
    confirmedRubinTractCount: kidsProduct.releasedGoldSupport.rubinOverlapTractCount,
    confirmedRubinTractIds: kidsProduct.releasedGoldSupport.rubinOverlapTractIds,
    productName: "KiDS-1000 released lensing-source support",
    release: kidsProduct.release,
    coverageSemantics: kidsProduct.coverageSemantics,
    eligibleAsFullRegistryFootprint: false,
  } : null;
  const hscLensingProduct = hscPublicProductsData.products.find((product) => product.surveyId === "hsc-lensing") as ResolvedProductFootprint | undefined;
  const scienceProducts = productIndexData.products.filter((product) => product.scienceReady);
  const viewerProducts = productIndexData.products.filter((product) => product.viewerReady);
  const pixelReadySurveyIdsByTract = Object.fromEntries(
    [...new Set(scienceProducts.map((product) => product.tract))].map((tract) => [
      tract,
      [...new Set(scienceProducts.filter((product) => product.tract === tract).map((product) => product.surveyId))],
    ]),
  );
  // Clicking a tract should show its pixels, not just describe them. The base
  // preview is the Rubin image from the optical comparison; the thumbnails are
  // every other measured layer at the same position.
  type PlacedProduct = { tract: number; surveyId: string; surveyName?: string; bandOrObservable?: string; referenceBand?: string; referenceImage?: string; skyPlacement?: unknown };
  const geometryLayers = (productIndexData.products as unknown as PlacedProduct[])
    .filter((product) => product.skyPlacement && product.referenceImage)
    .map((product) => ({
      tract: product.tract,
      surveyId: product.surveyId,
      surveyName: product.surveyName ?? product.surveyId,
      band: String(product.bandOrObservable ?? product.referenceBand ?? ""),
      image: product.referenceImage as string,
    }));
  const previewByTract: Record<number, string> = {};
  const layerThumbnailsByTract: Record<number, { surveyName: string; band: string; image: string }[]> = {};
  for (const product of productIndexData.products) {
    if (product.viewerReady && product.rubinImage && previewByTract[product.tract] === undefined) {
      previewByTract[product.tract] = product.rubinImage;
    }
  }
  for (const layer of geometryLayers) {
    if (layer.surveyId === "legacy-surveys-dr10") continue;
    (layerThumbnailsByTract[layer.tract] ??= []).push({
      surveyName: layer.surveyName,
      band: String(layer.band),
      image: layer.image,
    });
  }

  const { coverage, surveys } = buildCoverageExplorerData(
    footprintData as unknown as RubinFootprintFile,
    overlapData as unknown as ExternalOverlapFile,
    selectedRegionsData as unknown as SelectedRegionsFile,
    surveyRegistryData as unknown as SurveyRegistry,
    cacheManifestData as unknown as CacheManifestFile,
    {
      imageReadyTractIds: [...new Set(scienceProducts.map((product) => product.tract))],
      alignedViewerTractIds: [...new Set(viewerProducts.map((product) => product.tract))],
      pixelReadySurveyIdsByTract,
      previewByTract,
      layerThumbnailsByTract,
      scienceInputCandidateCount: productIndexData.summary.scienceReadyCount + rubinPixelsData.summary.scienceReadyRegionCount,
      validatedScienceInputCount: productIndexData.summary.scienceReadyCount + rubinPixelsData.summary.scienceReadyRegionCount,
      resolvedProductFootprints: [
        ...largeFootprintResolutionData.resolved,
        ...conservativeSubsetData.surveyEvidence,
        desiDr1ResolutionData,
        ...(kidsResolvedProduct ? [kidsResolvedProduct] : []),
        ...(hscLensingProduct ? [hscLensingProduct] : []),
      ] as ResolvedProductFootprint[],
    },
  );

  return (
    <main id="top">
      <header className="layers-header">
        <Link className="layers-brand" href="/"><span className="brand-glyph"><i /><b /></span><strong>Layers</strong><small>science comparison workspace</small></Link>
        <nav><Link href="/data">Download the catalogue</Link><Link href="/explorer">Difference explorer</Link><Link href="/differences">Cross-survey differences</Link><Link href="/pilots">Live multi-survey pilots</Link><Link href="/prototype">Aligned optical prototype</Link><Link href="/workspace">SPARC workspace</Link><a href="#dataset-registry-title">Dataset registry</a><a href="/api/coverage">Coverage API</a></nav>
        <span className="release-chip">DP2 · TRACT INDEX</span>
      </header>
      <CoverageExplorer coverage={coverage} surveys={surveys} objectiveAudit={goalAuditData} />
    </main>
  );
}
