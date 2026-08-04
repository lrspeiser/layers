import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { galaxies, getGalaxy } from "@/lib/galaxies";

export function generateStaticParams() {
  return galaxies.map((galaxy) => ({ slug: galaxy.slug }));
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const { slug } = await params;
  const galaxy = getGalaxy(slug);
  if (!galaxy) return {};
  return {
    title: galaxy.name,
    description: `${galaxy.name}: a demonstration object record in the Rubin Missing Light Atlas.`,
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
        <span className="release-pill"><span className="live-dot" /> Demonstration record</span>
      </header>

      <section className="record-masthead">
        <div>
          <span className="object-id">{galaxy.catalog} · PERMANENT OBJECT RECORD</span>
          <h1>{galaxy.name}</h1>
          <p>{galaxy.morphology} in {galaxy.constellation} · {galaxy.distance}</p>
        </div>
        <div className="record-actions">
          <a className="button button-primary" href="/sample-package.json" download>Download package ↓</a>
          <button className="button button-outline" type="button">Copy citation</button>
        </div>
      </section>

      <section className="record-layout">
        <div className="record-image">
          <img src="/rubin-virgo.jpg" alt={`Rubin survey illustration for ${galaxy.name}`} style={{ objectPosition: galaxy.crop }} />
          <span className="record-image-label">RUBIN · RGB · DIFFUSE-LIGHT OPTIMIZED</span>
          <span className="image-credit">NSF–DOE Vera C. Rubin Observatory / NOIRLab</span>
        </div>
        <aside className="record-summary">
          <span className="card-label">LEGACY DISCREPANCY CARD · PROTOTYPE</span>
          <h2>The visible disk extends beyond the legacy measurement.</h2>
          <p>Illustrative processing recovers low-surface-brightness structure outside the published optical radius. This record demonstrates how the atlas will separate measurement from cosmological interpretation.</p>
          <div className="record-metrics">
            <div><span>Outer radius</span><strong>{galaxy.diskDelta}</strong></div>
            <div><span>Δg<sub>bar</sub></span><strong>{galaxy.gravityDelta}</strong></div>
            <div><span>Inclination</span><strong>{galaxy.inclination}</strong></div>
            <div><span>Confidence</span><strong>{galaxy.confidence}%</strong></div>
          </div>
          <div className="review-badge"><i /> {galaxy.status} · independent review pending</div>
        </aside>
      </section>

      <section className="record-evidence">
        <div className="evidence-title"><span className="section-index">MEASUREMENT EVIDENCE</span><h2>Everything needed to reproduce the claim.</h2></div>
        <div className="evidence-cards">
          <article><span>01</span><h3>Images</h3><p>Six calibrated bands at five physical scales, plus star-removed and diffuse-light products.</p><strong>18 FITS products →</strong></article>
          <article><span>02</span><h3>Profiles</h3><p>Surface brightness, color, mass density, isophotes, and local background with uncertainty.</p><strong>7 measurement tables →</strong></article>
          <article><span>03</span><h3>Validation</h3><p>Injection–recovery completeness, sky-model sensitivity, and independent-pipeline agreement.</p><strong>{galaxy.confidence}% recovery →</strong></article>
          <article><span>04</span><h3>Provenance</h3><p>Inputs, software versions, parameters, checksums, masks, and human-review history.</p><strong>Full audit log →</strong></article>
        </div>
      </section>

      <div className="record-notice"><strong>Prototype notice</strong><p>This interface demonstrates the intended data structure. Numerical changes shown here are illustrative and are not published scientific results.</p></div>

      <footer>
        <Link className="brand footer-brand" href="/"><span className="brand-mark" aria-hidden="true"><i /></span><span>Rubin <strong>Missing Light</strong> Atlas</span></Link>
        <p>Prototype concept · Not affiliated with Rubin Observatory</p>
        <div><Link href="/#method">Method</Link><Link href="/#data">Data</Link></div>
      </footer>
    </main>
  );
}
