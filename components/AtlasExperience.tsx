"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { galaxies } from "@/lib/galaxies";
import { GalaxyComparison } from "@/components/GalaxyComparison";
import { manifestUrl } from "@/lib/atlas-manifest";

export function AtlasExperience() {
  const [query, setQuery] = useState("");
  const [selectedSlug, setSelectedSlug] = useState(galaxies[0].slug);
  const [verifiedTargets, setVerifiedTargets] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    Promise.all(
      galaxies.map((galaxy) =>
        fetch(manifestUrl(galaxy.slug))
          .then((response) => response.ok ? response.json() : null)
          .then((manifest) => manifest?.objectId === galaxy.slug && manifest?.verified === true),
      ),
    ).then((states) => setVerifiedTargets(new Set(galaxies.filter((_, index) => states[index]).map((galaxy) => galaxy.slug))))
      .catch(() => setVerifiedTargets(new Set()));
  }, []);

  const filtered = useMemo(
    () => galaxies.filter((galaxy) =>
      `${galaxy.name} ${galaxy.catalog} ${galaxy.constellation}`.toLowerCase().includes(query.toLowerCase()),
    ),
    [query],
  );
  const selectedGalaxy = galaxies.find((galaxy) => galaxy.slug === selectedSlug) ?? galaxies[0];

  return (
    <main id="top">
      <header className="site-header">
        <Link className="brand" href="#top" aria-label="Rubin Missing Light Atlas home">
          <span className="brand-mark" aria-hidden="true"><i /></span>
          <span>Rubin <strong>Missing Light</strong> Atlas</span>
        </Link>
        <nav aria-label="Primary navigation">
          <a href="#atlas">Atlas</a>
          <a href="#method">Method</a>
          <a href="#data">Data contract</a>
          <a href="#about">About</a>
        </nav>
        <a className="release-pill" href="#release-notes"><span className="live-dot" /> EDP2 pilot</a>
      </header>

      <section className="release-console" id="atlas">
        <div className="release-title">
          <span className="section-index">EARLY DATA PREVIEW 2 · 27 JUL 2026</span>
          <h1>Compare verified pixels, galaxy by galaxy.</h1>
          <p>Every slider is wired to its own cutout manifest. If the real Rubin and legacy inputs are absent, the atlas shows no image and makes the missing step explicit.</p>
        </div>
        <div className="release-facts" aria-label="Release facts">
          <div><strong>{verifiedTargets.size} / {galaxies.length}</strong><span>complete comparisons</span></div>
          <div><strong>5 / 5</strong><span>SPARC profiles loaded</span></div>
          <div><strong>4 / 5</strong><span>Spitzer IRAC1 images</span></div>
          <div><strong>0 / 5</strong><span>Rubin coverage queried</span></div>
        </div>
      </section>

      <section className="atlas-section atlas-first">
        <div className="workspace">
          <aside className="catalog-panel">
            <div className="catalog-head">
              <span className="section-index">SPARC PILOT QUEUE</span>
              <strong>Select a target</strong>
            </div>
            <div className="search-wrap">
              <span aria-hidden="true">⌕</span>
              <input aria-label="Search galaxies" placeholder="Search target or catalog" value={query} onChange={(event) => setQuery(event.target.value)} />
            </div>
            <div className="catalog-meta"><span>{filtered.length} targets · SPARC loaded</span><span>EDP2 unchecked</span></div>
            <div className="galaxy-list">
              {filtered.map((galaxy, index) => (
                <button
                  type="button"
                  className={`galaxy-row ${selectedGalaxy.slug === galaxy.slug ? "selected" : ""}`}
                  onClick={() => setSelectedSlug(galaxy.slug)}
                  aria-pressed={selectedGalaxy.slug === galaxy.slug}
                  key={galaxy.slug}
                >
                  <span className="target-index">{String(index + 1).padStart(2, "0")}</span>
                  <span className="galaxy-identity"><strong>{galaxy.name}</strong><small>{galaxy.catalog} · {galaxy.distance}</small></span>
                  <span className="galaxy-signal"><strong>{verifiedTargets.has(galaxy.slug) ? "VERIFIED" : galaxy.legacyState === "ready" ? "LEGACY READY" : "SPARC ONLY"}</strong><small>{galaxy.legacyState === "ready" ? "IRAC1 + profile" : "No SEIP image"}</small></span>
                  <span className={`status-mark ${verifiedTargets.has(galaxy.slug) ? "status-analyzed" : galaxy.legacyState === "ready" ? "status-review" : "status-queued"}`} aria-label={verifiedTargets.has(galaxy.slug) ? "Verified" : galaxy.legacyState === "ready" ? "Legacy data ready" : "SPARC profile only"} />
                </button>
              ))}
              {filtered.length === 0 && <p className="empty-state">No targets match this search.</p>}
            </div>
            <div className="catalog-foot">
              <span>Official SPARC data loaded for all five targets.</span>
              <span>Real Spitzer cutouts for four · Rubin authentication still required.</span>
            </div>
          </aside>

          <article className="object-panel" id={selectedGalaxy.slug}>
            <div className="object-head">
              <div>
                <span className="object-id">{selectedGalaxy.catalog} · EDP2 INGEST RECORD</span>
                <h2>{selectedGalaxy.name}</h2>
                <p>{selectedGalaxy.morphology} · {selectedGalaxy.constellation} · {selectedGalaxy.distance}</p>
              </div>
              <Link className="open-record" href={`/galaxy/${selectedGalaxy.slug}`}>Open record <span>↗</span></Link>
            </div>
            <GalaxyComparison galaxy={selectedGalaxy} />
          </article>
        </div>
      </section>

      <section className="method-section" id="method">
        <div className="method-copy">
          <span className="section-index">THE SCIENCE GATE</span>
          <h2>A difference is not a result until the baseline is measured.</h2>
          <p>The atlas will label a change “large” only from its measured uncertainty and a declared cross-survey baseline. Filter response, PSF, sky subtraction, masks, and registration are reconciled before profiles are compared.</p>
          <a className="text-link light" href="#data">Inspect the ingest contract <span>→</span></a>
        </div>
        <div className="method-flow">
          <div><span>01</span><strong>Locate</strong><p>Query EDP2 deep-coadd patches that overlap a predeclared target field.</p></div>
          <div><span>02</span><strong>Reproject</strong><p>Mosaic every band and legacy input onto one target-centered WCS.</p></div>
          <div><span>03</span><strong>Match</strong><p>Reconcile PSF, sky, masks, photometric scale, and filter choice.</p></div>
          <div><span>04</span><strong>Verify</strong><p>Reject duplicate pixels, failed astrometry, missing provenance, and uncalibrated claims.</p></div>
        </div>
      </section>

      <section className="data-section" id="data">
        <div className="data-copy">
          <span className="section-index">PER-OBJECT DATA CONTRACT</span>
          <h2>One manifest turns each target on.</h2>
          <p>The included RSP pipeline exports Rubin bands, dataset UUIDs, WCS, variance and masks. A separate alignment step adds the legacy image. Only a manifest marked verified is allowed into the slider.</p>
          <div className="data-actions">
            <a className="button button-dark" href="https://github.com/lrspeiser/rubin-light-atlas/tree/main/pipeline">View ingest pipeline ↗</a>
            <a className="text-link" href="#release-notes">Read release limits →</a>
          </div>
        </div>
        <div className="package-card">
          <div className="package-head"><span>public/atlas/ngc-300/</span><span>verified only</span></div>
          <div className="file-tree">
            <span><i>JSON</i>manifest.json</span>
            <span><i>FITS</i>rubin_u · g · r · i · z · y</span>
            <span><i>FITS</i>variance · mask · WCS</span>
            <span><i>FITS</i>legacy_registered</span>
            <span><i>WEB</i>rubin_rgb · legacy_rgb</span>
            <span><i>JSON</i>registration_qa.json</span>
            <span><i>SHA256</i>source checksums</span>
          </div>
          <div className="package-foot"><span>✓ unique datasets</span><span>✓ aligned grids</span><span>✓ provenance</span></div>
        </div>
      </section>

      <section className="release-notes" id="release-notes">
        <div><span className="section-index">WHAT JULY 2026 ACTUALLY CONTAINS</span><h2>Start with coadds—not the whole release.</h2></div>
        <div className="release-note-grid">
          <article><strong>Available now</strong><p>Early DP2 deep coadded images and catalogs, processed from LSSTCam observations taken April 2025 through January 2026.</p></article>
          <article><strong>Not available yet</strong><p>Raw, visit, template, and difference images are scheduled for the complete DP2 release later in 2026.</p></article>
          <article><strong>Access boundary</strong><p>Rubin data rights and an authenticated Rubin Science Platform session are currently required. Credentials never belong in this repository.</p></article>
          <article><strong>Coverage test</strong><p>Each target must be queried against the actual EDP2 footprint. A familiar catalog name is not proof that its sky position is covered.</p></article>
        </div>
      </section>

      <section className="closing" id="about">
        <span className="closing-orbit" aria-hidden="true"><i /></span>
        <p>A neutral measurement layer for the visible universe.</p>
        <h2>No borrowed pixels.<br />No unexplained numbers.</h2>
        <a className="button button-primary" href="#atlas">Return to targets <span>↑</span></a>
      </section>

      <footer>
        <Link className="brand footer-brand" href="#top"><span className="brand-mark" aria-hidden="true"><i /></span><span>Rubin <strong>Missing Light</strong> Atlas</span></Link>
        <p>Independent prototype · Not affiliated with Rubin Observatory</p>
        <div><a href="#method">Method</a><a href="#data">Data contract</a></div>
      </footer>
    </main>
  );
}
