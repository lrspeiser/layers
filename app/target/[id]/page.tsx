import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import catalogData from "@/public/data/layers-catalog.json";
import type { LayerTarget } from "@/lib/layers";
import { layerStatusLabel } from "@/lib/layers";
import TargetComparisonViewer from "@/components/TargetComparisonViewer";
import previewData from "@/public/data/comparison-previews.json";

const targets = catalogData.targets as unknown as LayerTarget[];

export function generateStaticParams() {
  return targets.map((target) => ({ id: target.id }));
}

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  const target = targets.find((item) => item.id === id);
  return target ? { title: target.name, description: `${target.name}: a permanent target and layer record in Layers.` } : {};
}

export default async function TargetPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const target = targets.find((item) => item.id === id);
  if (!target) notFound();
  const rubin = target.layers.find((layer) => layer.id === "rubin-dp2-deep-coadd");
  const pilot = target.pilotAudit;
  const publishedComparisons = target.comparisons.filter((comparison) => comparison.status === "published");
  const comparisonPreviews = target.comparisons.flatMap((comparison) => {
    const preview = previewData.comparisons.find((item) =>
      item.objectId === target.id && item.layerIds.every((layerId) => comparison.layerIds.includes(layerId)),
    );
    return preview ? [{ comparison, preview }] : [];
  });

  return (
    <main className="target-record-page">
      <header className="layers-header">
        <Link className="layers-brand" href="/"><span className="brand-glyph"><i /><b /></span><strong>Layers</strong><small>science comparison workspace</small></Link>
        <nav><Link href="/#workspace">Workspace</Link><Link href="/#method">Method</Link><Link href={`/api/targets/${target.id}`}>API record</Link></nav>
        <span className="release-chip">PERMANENT TARGET</span>
      </header>

      <section className="record-hero">
        <div><span className="eyebrow">{target.selection.sample} · {target.id}</span><h1>{target.name}</h1><p>{target.identifiers.SIMBAD} · ICRS {target.center.raDeg.toFixed(6)}°, {target.center.decDeg.toFixed(6)}° · {target.region.widthArcmin}′ field</p></div>
        <Link href={`/#workspace`}>← Back to workspace</Link>
      </section>

      <section className="page-brief record-page-brief" aria-label="Goal, details, and issues">
        <article><span>GOAL</span><h3>Audit this sky position</h3><p>Keep every available image, catalog, and profile layer for {target.name} in one reproducible record.</p></article>
        <article><span>DETAILS</span><h3>{target.layers.length} layers · {target.comparisons.length} comparisons</h3><p>{rubin?.availability === "available-local" ? "Usable local Rubin pixels are verified for this separate field." : rubin?.note ?? "Coverage state recorded."}</p></article>
        <article className="brief-issue"><span>ISSUES</span><h3>{publishedComparisons.length ? "Published results have limits" : "No image difference published"}</h3><p>{pilot?.observation ?? "Cross-layer claims remain blocked until the declared reconciliation and QA gates pass."}</p></article>
      </section>

      <section className="record-content">
        <div className="record-main">
          {comparisonPreviews.map(({ comparison, preview }) => <TargetComparisonViewer key={comparison.id} target={target} comparison={comparison} preview={preview} />)}
          <div className="record-section-title"><span className="eyebrow">AVAILABLE LAYERS</span><h2>Evidence attached to this place in the sky.</h2></div>
          <div className="record-layer-list">
            {target.layers.map((layer, index) => (
              <article key={layer.id}>
                <span className="record-layer-index">{String(index + 1).padStart(2, "0")}</span>
                <div><span className="eyebrow">{layer.kind} · {layer.renderMode}</span><h3>{layer.survey}</h3><p>{layer.release} · {layer.instrument}</p><small>{layer.note}</small></div>
                <div className="record-layer-state"><strong>{layerStatusLabel(layer)}</strong><span>{layer.bands.join(" · ") || "No image bands"}</span><span>{layer.datasetCount ?? "—"} datasets</span></div>
              </article>
            ))}
          </div>

          <div className="record-section-title"><span className="eyebrow">COMPARISONS</span><h2>Published only after reconciliation and QA.</h2></div>
          <div className="record-empty-comparison"><strong>{target.comparisons.length}</strong><div><h3>{publishedComparisons.length ? `${publishedComparisons.length} scientific comparison${publishedComparisons.length === 1 ? " is" : "s are"} published.` : target.comparisons.length ? "QA comparison records are available; no scientific difference is published." : "No cross-layer comparison record yet."}</h3><p>{comparisonPreviews.length ? "Rubin image comparisons remain QA-only; no Rubin optical scientific difference is published. " : ""}Images require common WCS, footprint, PSF, filter response, units, masks, background, uncertainty, and astrometric checks. Catalog/profile comparisons retain physical quantities, their declared models, expected range, and cross-survey uncertainty.</p></div></div>
          {publishedComparisons.map((comparison) => <article className="record-published-comparison" key={comparison.id}>
            <div><span className="eyebrow">PUBLISHED · {comparison.comparisonMode ?? "image"}</span><h3>{comparison.measurements[0]?.label ?? comparison.id}</h3><p>{comparison.inferences[0]?.observation}</p></div>
            <div><strong>{comparison.measurements[0]?.value > 0 ? "+" : ""}{comparison.measurements[0]?.value.toFixed(3)} {comparison.measurements[0]?.unit}</strong><span>{comparison.measurements[0]?.classification} · {comparison.measurements[0]?.significanceSigma.toFixed(2)}σ</span><a href={comparison.products?.qaPackage}>Download package ↗</a></div>
          </article>)}
          {pilot && <section className="record-pilot-audit">
            <div className="panel-heading"><span className="eyebrow">PILOT OUTCOME · {pilot.stage}</span><span className="claim-state qa-fail">CLAIMS BLOCKED</span></div>
            <h3>{pilot.observation}</h3>
            <div className="record-pilot-metric"><span><small>{pilot.metric.label}</small><strong>{pilot.metric.value.toFixed(3)} {pilot.metric.unit}</strong></span><span><small>declared requirement</small><strong>{pilot.metric.comparison} {pilot.metric.passThreshold.toFixed(2)}</strong></span></div>
            <p><strong>Next action:</strong> {pilot.nextAction}</p>
            <a href={`/data/pilot-audits/${target.id}.json`}>Download pilot audit + checksums ↗</a>
          </section>}
        </div>

        <aside className="record-aside">
          <span className="eyebrow">CURRENT AUDIT</span>
          <h2>{rubin?.availability === "no-valid-pixels" ? "Footprint false positive" : rubin?.availability === "available-local" ? "Local Rubin pixels verified" : "Coverage state recorded"}</h2>
          <p>{rubin?.note}</p>
          <dl><div><dt>Selection</dt><dd>{target.selection.sample}</dd></div><div><dt>Major axis</dt><dd>{target.selection.majorAxisArcmin?.toFixed(2) ?? "—"}′</dd></div><div><dt>Layers</dt><dd>{target.layers.length}</dd></div><div><dt>Pilot gate</dt><dd>{pilot?.outcome ?? "not in pilot"}</dd></div><div><dt>Published comparisons</dt><dd>{publishedComparisons.length}</dd></div></dl>
          <div className="record-principle"><strong>Observation ≠ inference</strong><p>This record separates acquired evidence, measured differences, model-dependent interpretation, and speculation.</p></div>
          <a href={`/api/targets/${target.id}`}>Download machine-readable record ↗</a>
        </aside>
      </section>

      <footer><Link className="layers-brand" href="/"><span className="brand-glyph"><i /><b /></span><strong>Layers</strong></Link><p>Independent scientific prototype · No fabricated pixels or claims.</p><div><a href="https://github.com/lrspeiser/layers">Source ↗</a><a href="/api/catalog">Catalog API</a></div></footer>
    </main>
  );
}
