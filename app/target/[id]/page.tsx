import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import catalogData from "@/public/data/layers-catalog.json";
import type { LayerTarget } from "@/lib/layers";
import { layerStatusLabel } from "@/lib/layers";

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

      <section className="record-content">
        <div className="record-main">
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
          <div className="record-empty-comparison"><strong>{target.comparisons.length}</strong><div><h3>No publishable cross-layer comparison yet.</h3><p>Images will activate the swipe view only after common WCS, common footprint, PSF, filter response, units, masks, background, and astrometric residual checks pass. Profiles remain linked plots.</p></div></div>
        </div>

        <aside className="record-aside">
          <span className="eyebrow">CURRENT AUDIT</span>
          <h2>{rubin?.availability === "no-valid-pixels" ? "Footprint false positive" : rubin?.availability === "available-local" ? "Local Rubin pixels verified" : "Coverage state recorded"}</h2>
          <p>{rubin?.note}</p>
          <dl><div><dt>Selection</dt><dd>{target.selection.sample}</dd></div><div><dt>Major axis</dt><dd>{target.selection.majorAxisArcmin?.toFixed(2) ?? "—"}′</dd></div><div><dt>Layers</dt><dd>{target.layers.length}</dd></div><div><dt>Claims</dt><dd>0</dd></div></dl>
          <div className="record-principle"><strong>Observation ≠ inference</strong><p>This record separates acquired evidence, measured differences, model-dependent interpretation, and speculation.</p></div>
          <a href={`/api/targets/${target.id}`}>Download machine-readable record ↗</a>
        </aside>
      </section>

      <footer><Link className="layers-brand" href="/"><span className="brand-glyph"><i /><b /></span><strong>Layers</strong></Link><p>Independent scientific prototype · No fabricated pixels or claims.</p><div><a href="https://github.com/lrspeiser/rubin-light-atlas">Source ↗</a><a href="/api/catalog">Catalog API</a></div></footer>
    </main>
  );
}
