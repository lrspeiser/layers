import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { galaxies, getGalaxy } from "@/lib/galaxies";
import { GalaxyRecordComparison } from "@/components/GalaxyRecordComparison";

export function generateStaticParams() {
  return galaxies.map((galaxy) => ({ slug: galaxy.slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  const galaxy = getGalaxy(slug);
  if (!galaxy) return {};
  return {
    title: galaxy.name,
    description: `${galaxy.name}: an EDP2 ingest record in the Rubin Missing Light Atlas.`,
  };
}

export default async function GalaxyPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const galaxy = getGalaxy(slug);
  if (!galaxy) notFound();

  return (
    <main className="record-page">
      <header className="site-header record-header">
        <Link className="brand" href="/">
          <span className="brand-mark" aria-hidden="true"><i /></span>
          <span>Rubin <strong>Missing Light</strong> Atlas</span>
        </Link>
        <Link className="back-link" href="/#atlas">← Back to atlas</Link>
        <span className="release-pill"><span className="live-dot" /> EDP2 ingest record</span>
      </header>

      <section className="record-masthead">
        <div>
          <span className="object-id">{galaxy.catalog} · PERMANENT TARGET RECORD</span>
          <h1>{galaxy.name}</h1>
          <p>{galaxy.morphology} in {galaxy.constellation} · {galaxy.distance} · RA {galaxy.raDeg.toFixed(5)}° · Dec {galaxy.decDeg.toFixed(5)}°</p>
        </div>
        <div className="record-actions">
          <a className="button button-primary" href="https://github.com/lrspeiser/rubin-light-atlas/tree/main/pipeline">Run ingest ↗</a>
          <Link className="button button-outline" href="/#data">Manifest contract</Link>
        </div>
      </section>

      <section className="record-layout">
        <GalaxyRecordComparison galaxy={galaxy} />
        <aside className="record-summary">
          <span className="card-label">MEASUREMENT STATE · NOT YET PUBLISHED</span>
          <h2>No numerical discrepancy is asserted for this target.</h2>
          <p>The record is intentionally gated. The July 2026 EDP2 coverage query, unique Rubin cutout, legacy registration, PSF/sky reconciliation, and validation report must all exist before images or statistics appear here.</p>
          <div className="record-metrics">
            <div><span>EDP2 coverage</span><strong>—</strong><small className="record-signal signal-pending">Unchecked</small></div>
            <div><span>Image manifest</span><strong>—</strong><small className="record-signal signal-pending">Missing</small></div>
            <div><span>Registration QA</span><strong>—</strong><small className="record-signal signal-pending">Not run</small></div>
            <div><span>Difference stats</span><strong>—</strong><small className="record-signal signal-pending">Blocked</small></div>
          </div>
          <p className="record-benchmark">A future “large difference” label will require a ≥3σ change relative to a declared cross-survey baseline. The uncertainty and baseline will be published alongside the value.</p>
          <div className="review-badge"><i /> awaiting authenticated EDP2 ingest</div>
        </aside>
      </section>

      <section className="record-evidence">
        <div className="evidence-title"><span className="section-index">REQUIRED EVIDENCE</span><h2>Everything needed to turn this slot into a result.</h2></div>
        <div className="evidence-cards">
          <article><span>01</span><h3>Rubin pixels</h3><p>All available EDP2 deep-coadd bands, variance, mask, WCS, PSF metadata, and Butler dataset UUIDs.</p><strong>AUTHENTICATED QUERY</strong></article>
          <article><span>02</span><h3>Legacy pixels</h3><p>A named older survey product with source identifiers, calibration, filter response, and checksums.</p><strong>EXPLICIT SOURCE</strong></article>
          <article><span>03</span><h3>Alignment</h3><p>Common WCS and pixel grid, plus measured astrometric residual, PSF matching, and sky reconciliation.</p><strong>QA MUST PASS</strong></article>
          <article><span>04</span><h3>Significance</h3><p>Change, uncertainty, comparison baseline, σ score, injection–recovery performance, and review state.</p><strong>NO NAKED PERCENTAGES</strong></article>
        </div>
      </section>

      <div className="record-notice"><strong>Honest empty state</strong><p>This target page does not reuse the commissioning image or display illustrative measurements. It will activate automatically when a verified per-object manifest is published.</p></div>

      <footer>
        <Link className="brand footer-brand" href="/"><span className="brand-mark" aria-hidden="true"><i /></span><span>Rubin <strong>Missing Light</strong> Atlas</span></Link>
        <p>Independent prototype · Not affiliated with Rubin Observatory</p>
        <div><Link href="/#method">Method</Link><Link href="/#data">Data contract</Link></div>
      </footer>
    </main>
  );
}
