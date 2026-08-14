import type { Metadata } from "next";
import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import { TractLayerStack, type TractLayerProduct } from "@/components/TractLayerStack";
import { TractProductShelf } from "@/components/TractProductShelf";
import { OnDemandRubinCutout } from "@/components/OnDemandRubinCutout";
import { getRubinCutoutJob, getRubinWorkerStatus } from "@/lib/rubin-on-demand";
import footprintData from "@/public/data/coverage/rubin-dp2-footprint.json";
import overlapData from "@/public/data/coverage/external-overlaps.json";
import selectedData from "@/public/data/coverage/selected-regions.json";
import acquisitionData from "@/public/data/coverage/acquisition-50-summary.json";
import subsetData from "@/public/data/coverage/conservative-subset-evidence.json";
import registryData from "@/public/data/survey-registry.json";
import legacyData from "@/public/data/layers/selected-regions/legacy-dr10.json";
import familyManifestData from "@/public/data/layers/family-examples/tract-manifest.json";
import productIndexData from "@/public/data/layers/tract-product-index.json";
import largeFootprintResolutionData from "@/public/data/coverage/large-footprint-resolution.json";
import hscKidsGapAuditData from "@/public/data/coverage/hsc-kids-gap-audit.json";
import hscPublicProductsData from "@/public/data/coverage/hsc-public-products.json";
import desiDr1ResolutionData from "@/public/data/coverage/desi-dr1-resolution.json";
import styles from "./tract.module.css";

type PageProps = { params: Promise<{ tract: string }> };
type LegacyRecord = {
  regionId: string; tract: number; center: number[]; status: string; scienceReady: boolean; comparisonReady: boolean;
  band?: string; unit?: string; validPixelFraction?: number; preview?: string; normalizedFits?: { sha256?: string; bytes?: number };
  reason?: string; comparisonBlockers?: string[];
};
type IndexedProduct = TractLayerProduct & {
  tract: number; surveyId: string; viewerReady: boolean; scienceReady: boolean; displayReady: boolean;
  productType: string; referenceImage?: string | null; status: string;
};
type ResolvedProduct = {
  surveyId: string; productName: string; release: string; coverageSemantics: string;
  eligibleAsFullRegistryFootprint: boolean; confirmedRubinTractIds: number[];
};

const pilotByTract: Record<number, { fieldId: string; label: string }> = {
  10689: { fieldId: "ugc00634", label: "UGC00634" },
  11162: { fieldId: "ugc00191", label: "UGC00191" },
  11411: { fieldId: "ugc00891", label: "UGC00891" },
};

const subsetLabels: Record<string, string> = {
  "alfalfa-alpha100": "ALFALFA α.100 detections",
  alma: "PHANGS–ALMA released-product subset",
  "resolved-hi-archives": "THINGS / LITTLE THINGS target subset",
};

function tractNumber(value: string) {
  return /^\d+$/.test(value) ? Number(value) : Number.NaN;
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { tract } = await params;
  return { title: `Rubin tract ${tract}`, description: `Real archive evidence, images, and overlap readiness for Rubin DP2 tract ${tract}.` };
}

export default async function TractPage({ params }: PageProps) {
  const { tract: tractParam } = await params;
  const tract = tractNumber(tractParam);
  const footprint = (footprintData.tracts as unknown as Array<[number, [number, number], unknown, number, number[]]>).find((row) => row[0] === tract);
  if (!footprint) notFound();

  const overlap = (overlapData.tracts as unknown as Array<[number, string[], string[], string[], string[]]>).find((row) => row[0] === tract);
  const surveyNames = new Map(registryData.surveys.map((survey) => [survey.id, survey.shortName || survey.name]));
  const selected = selectedData.regions.find((region) => region.tract === tract);
  const regionId = `dp2-tract-${tract}`;
  const legacy = (legacyData.regions as unknown as LegacyRecord[]).find((record) => record.tract === tract);
  const pilot = pilotByTract[tract];
  const subset = (subsetData.tracts as unknown as Array<[number, string[], string[]]>).find((row) => row[0] === tract);
  const familyRoute = familyManifestData.routes.find((route) => route.tract === tract);
  const metadata = acquisitionData.metadataResponses.filter((record) => record.regionId === regionId);
  const rubinDiscovery = metadata.find((record) => record.surveyId === "rubin-dp2" && record.phase === "discover");
  const resolvedProducts = ([...largeFootprintResolutionData.resolved, desiDr1ResolutionData] as ResolvedProduct[]).filter((product) => product.confirmedRubinTractIds.includes(tract));
  const kidsProduct = hscKidsGapAuditData.products.find((product) => product.surveyId === "kids-1000-lensing") as unknown as ({
    surveyId: string; release: string; coverageSemantics: string;
    releasedGoldSupport: { rubinOverlapTractIds: number[] };
  } | undefined);
  const kidsSubset: ResolvedProduct[] = kidsProduct?.releasedGoldSupport.rubinOverlapTractIds.includes(tract) ? [{
    surveyId: kidsProduct.surveyId,
    productName: "KiDS-1000 released lensing-source support",
    release: kidsProduct.release,
    coverageSemantics: kidsProduct.coverageSemantics,
    eligibleAsFullRegistryFootprint: false,
    confirmedRubinTractIds: kidsProduct.releasedGoldSupport.rubinOverlapTractIds,
  }] : [];
  const hscLensingSubsets = (hscPublicProductsData.products as ResolvedProduct[]).filter(
    (product) => product.confirmedRubinTractIds.includes(tract),
  );
  const fullResolvedProducts = resolvedProducts.filter((product) => product.eligibleAsFullRegistryFootprint);
  const namedProductSubsets = [
    ...resolvedProducts.filter((product) => !product.eligibleAsFullRegistryFootprint),
    ...kidsSubset,
    ...hscLensingSubsets,
  ];
  const exactOverlaps = [...new Set([...(overlap?.[1] ?? []), ...fullResolvedProducts.map((product) => product.surveyId)])];
  const conservativeSignals = (subset?.[2]?.length ?? 0) + namedProductSubsets.length;
  const cachedProducts = (productIndexData.products as unknown as IndexedProduct[]).filter(
    (product) => product.tract === tract && (product.scienceReady || product.displayReady),
  );
  const layerProducts = cachedProducts.filter((product) => product.viewerReady);
  const nonViewerProducts = cachedProducts.filter((product) => !product.viewerReady);
  const onDemandEligible = !layerProducts.length && !legacy?.scienceReady;
  // A prebuilt demonstration must not hide newer on-demand layers for the
  // same tract. Blob lookup is cheap and lets every tract page converge on the
  // richest validated product set generated by the queue.
  const [existingOnDemandJob, rubinWorkerStatus] = await Promise.all([
    getRubinCutoutJob(tract),
    onDemandEligible ? getRubinWorkerStatus() : Promise.resolve(null),
  ]);
  const rawOnDemandLayers = existingOnDemandJob?.layers?.length
    ? existingOnDemandJob.layers
    : existingOnDemandJob?.comparison ? [{ ...existingOnDemandJob.comparison, family: "optical" }] : [];
  const onDemandLayerProducts: TractLayerProduct[] = rawOnDemandLayers.map((layer) => ({
    id: `dp2-tract-${tract}-on-demand-${layer.surveyId}`,
    family: layer.family,
    surveyName: layer.surveyName,
    release: layer.release,
    rubinBand: layer.rubinBand,
    referenceBand: layer.referenceBand,
    referenceUnit: layer.referenceUnit,
    commonCoverageFraction: layer.commonCoverageFraction,
    rubinImage: layer.rubinImageUrl,
    referenceImage: layer.referenceImageUrl,
    coverageImage: layer.coverageImageUrl,
    overlayImage: layer.overlayImageUrl,
    interpretation: `The authenticated Rubin and ${layer.surveyName} pixels share a celestial display grid and common finite-pixel mask.`,
    blockers: layer.blockers,
  }));
  const onDemandSurveyNames = new Set(onDemandLayerProducts.map((product) => product.surveyName));
  const liveLayerProducts = [
    ...layerProducts.filter((product) => !onDemandSurveyNames.has(product.surveyName)),
    ...onDemandLayerProducts,
  ];
  const onDemandHistoricalProduct = onDemandLayerProducts.find((product) => product.family === "optical");
  const historicalProduct = layerProducts.find((product) => product.family === "optical");
  const ra = footprint[1][0];
  const dec = footprint[1][1];

  return (
    <main className={styles.shell}>
      <header className="layers-header">
        <Link className="layers-brand" href="/"><span className="brand-glyph"><i /><b /></span><strong>Layers</strong><small>science comparison workspace</small></Link>
        <nav><Link href="/">Full footprint</Link><Link href="/pilots">Live multi-survey pilots</Link><Link href="/prototype">Aligned optical prototype</Link></nav>
        <span className="release-chip">DP2 · TRACT {tract}</span>
      </header>

      <section className={styles.hero}>
        <div><span>TRACT EVIDENCE WORKSPACE</span><h1>Rubin tract {tract}</h1><p>RA {ra.toFixed(6)}° · Dec {dec.toFixed(6)}° · {footprint[3].toLocaleString()} indexed Rubin patches</p></div>
        <Link href={`/?tract=${tract}`}>Back to footprint →</Link>
      </section>

      <section className={styles.statusGrid} aria-label="Readiness summary">
        <article><span>RUBIN INDEX</span><strong>{rubinDiscovery ? `${rubinDiscovery.rowCount} datasets discovered` : "Footprint indexed"}</strong><small>{rubinDiscovery ? "Authenticated SIA evidence cached" : "Pixel acquisition not selected for this tract"}</small></article>
        <article className={legacy?.scienceReady || historicalProduct || onDemandHistoricalProduct ? styles.ready : ""}><span>HISTORICAL IMAGE</span><strong>{legacy?.scienceReady ? `Legacy DR10 ${legacy.band}-band ready` : historicalProduct ? `${historicalProduct.surveyName} ${historicalProduct.referenceBand}-band ready` : onDemandHistoricalProduct ? `${onDemandHistoricalProduct.surveyName} ${onDemandHistoricalProduct.referenceBand}-band ready` : "No validated local image"}</strong><small>{legacy?.scienceReady ? `${((legacy.validPixelFraction ?? 0) * 100).toFixed(2)}% valid weighted pixels` : historicalProduct ? `${(historicalProduct.commonCoverageFraction * 100).toFixed(2)}% common finite support after display alignment` : onDemandHistoricalProduct ? `${(onDemandHistoricalProduct.commonCoverageFraction * 100).toFixed(2)}% on-demand common support` : legacy?.reason ?? "Coverage evidence only"}</small></article>
        <article className={liveLayerProducts.length ? styles.ready : ""}><span>ALIGNED VIEWER</span><strong>{liveLayerProducts.length ? `${liveLayerProducts.length} external layer${liveLayerProducts.length === 1 ? "" : "s"} live` : "Alignment pending"}</strong><small>{liveLayerProducts.length ? liveLayerProducts.map((product) => product.surveyName).join(" · ") : "Requires two validated images on one WCS grid"}</small></article>
        <article><span>EXACT FOOTPRINTS</span><strong>{exactOverlaps.length} release/product overlaps</strong><small>{conservativeSignals ? `Plus ${conservativeSignals} conservative subset signal${conservativeSignals === 1 ? "" : "s"}` : "Subset evidence tracked separately"}</small></article>
      </section>

      {liveLayerProducts.length ? (
        <section className={styles.viewerSection}>
          <div className={styles.sectionHeading}><div><span>REAL COMMON-GRID PIXELS</span><h2>Rubin plus every cached layer at this position</h2></div><Link href={`/overlay/${tract}`}>Overlay every measured layer →</Link>{pilot && <Link href={`/pilots?field=${pilot.fieldId}&tract=${tract}`}>Open full pilot evidence →</Link>}</div>
          <TractLayerStack tract={tract} products={liveLayerProducts} />
          {onDemandLayerProducts.length > 0 && <OnDemandRubinCutout tract={tract} initialJob={existingOnDemandJob} workerStatus={rubinWorkerStatus} />}
        </section>
      ) : legacy?.scienceReady && legacy.preview ? (
        <section className={styles.viewerSection}>
          <div className={styles.sectionHeading}><div><span>VALIDATED HISTORICAL PIXELS</span><h2>Legacy DR10 {legacy.band}-band image</h2></div><em>Rubin alignment pending</em></div>
          <div className={styles.singleImage}>
            <figure><Image src={legacy.preview} width={512} height={512} alt={`Legacy DR10 ${legacy.band}-band image in Rubin tract ${tract}`} /><figcaption>Archive pixels · display stretch only</figcaption></figure>
            <div><h3>What is behind this image</h3><ul><li>Flux: {legacy.unit}</li><li>Inverse variance: present</li><li>Valid mask and coverage: derived from positive finite weight</li><li>Celestial WCS: validated</li><li>Checksum: {legacy.normalizedFits?.sha256?.slice(0, 16)}…</li></ul><p>This is science-input ready, but it is not yet a Rubin comparison. The page will add the swipe only after Rubin IMAGE, VARIANCE, and MASK pixels are aligned and the comparison gates are recorded.</p></div>
          </div>
        </section>
      ) : (
        <section className={styles.pending}><span>{existingOnDemandJob?.science ? "ON-DEMAND IMAGE READY" : "NO LOCAL IMAGE YET"}</span><h2>{existingOnDemandJob?.science ? "This tract now has validated Rubin pixels." : "This tract currently has coverage evidence, not display pixels."}</h2><p>That distinction is deliberate: a survey footprint can overlap the tract while the requested cutout contains no usable pixels or has not yet passed WCS, units, uncertainty, and mask validation.</p><OnDemandRubinCutout tract={tract} initialJob={existingOnDemandJob} workerStatus={rubinWorkerStatus} /></section>
      )}

      {nonViewerProducts.length > 0 && (
        <section className={styles.viewerSection}>
          <div className={styles.sectionHeading}><div><span>OTHER REAL ARCHIVE PRODUCTS</span><h2>Cached here; common-grid overlay still pending</h2></div><em>{nonViewerProducts.length} product{nonViewerProducts.length === 1 ? "" : "s"}</em></div>
          <TractProductShelf tract={tract} products={nonViewerProducts} />
        </section>
      )}

      <section className={styles.evidenceGrid}>
        <article><span>EXACT RELEASE / PRODUCT INTERSECTIONS</span><h2>{exactOverlaps.length} datasets</h2><div className={styles.chips}>{exactOverlaps.map((surveyId) => <i key={surveyId}>{surveyNames.get(surveyId) ?? surveyId}</i>)}</div><small>These are exact MOC or archive-product intersections. They establish released support, not usable pixels or a quantitative comparison at every position.</small></article>
        <article><span>CONSERVATIVE DETECTION / PROGRAM SUBSETS</span><h2>{conservativeSignals} signals</h2>{conservativeSignals ? <div className={styles.chips}>{subset?.[2]?.map((surveyId) => <i key={surveyId}>{subsetLabels[surveyId] ?? surveyId}</i>)}{namedProductSubsets.map((product) => <i key={product.surveyId}>{product.productName}</i>)}</div> : <p>No released catalog-position or named-program subset intersects this tract.</p>}<small>These prove a released detection or named program only. They are never counted as complete parent-survey footprints.</small></article>
        <article><span>ACQUISITION QUEUE</span><h2>{selected ? "Automatically selected" : "Not in the first 50"}</h2><p>{selected ? `${metadata.length} cached metadata responses. Selection reasons: ${selected.selectionReasons.join("; ")}.` : "The tract remains fully indexed and can enter a future on-demand cutout request."}</p></article>
        {familyRoute && <article><span>REAL NON-IMAGE EVIDENCE</span><h2>{familyRoute.families.join(" + ")}</h2><p>{familyRoute.evidence.length} real archive product{familyRoute.evidence.length === 1 ? "" : "s"} are available here as spectra or catalogs, with checksums and explicit interpretation limits.</p><Link href={familyRoute.href}>Inspect spectrum / catalog evidence →</Link></article>}
      </section>
    </main>
  );
}
