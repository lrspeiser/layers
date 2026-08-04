"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { galaxies } from "@/lib/galaxies";

const bands = ["RGB", "u", "g", "r", "i", "z", "y", "Diffuse"];
const profileLegacy = [78, 70, 62, 54, 47, 39, 31, 25, 19, 15, 12, 10];
const profileRubin = [80, 72, 64, 57, 50, 43, 36, 30, 25, 21, 18, 15];
const signalLabels = {
  large: "Large difference",
  above: "Above expected",
  expected: "Within expected",
  pending: "Pending",
};

export function AtlasExperience() {
  const [band, setBand] = useState("RGB");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("All");
  const [reveal, setReveal] = useState(52);
  const [selectedSlug, setSelectedSlug] = useState(galaxies[0].slug);

  const filtered = useMemo(
    () =>
      galaxies.filter((galaxy) => {
        const matchesQuery = `${galaxy.name} ${galaxy.catalog} ${galaxy.feature}`
          .toLowerCase()
          .includes(query.toLowerCase());
        const matchesStatus = status === "All" || galaxy.status === status;
        return matchesQuery && matchesStatus;
      }),
    [query, status],
  );

  const selectedGalaxy = galaxies.find((galaxy) => galaxy.slug === selectedSlug) ?? galaxies[0];
  const selectGalaxy = (slug: string) => {
    setSelectedSlug(slug);
    setReveal(52);
  };

  return (
    <main>
      <header className="site-header">
        <Link className="brand" href="#top" aria-label="Rubin Missing Light Atlas home">
          <span className="brand-mark" aria-hidden="true"><i /></span>
          <span>Rubin <strong>Missing Light</strong> Atlas</span>
        </Link>
        <nav aria-label="Primary navigation">
          <a href="#atlas">Atlas</a>
          <a href="#method">Method</a>
          <a href="#data">Data</a>
          <a href="#about">About</a>
        </nav>
        <a className="release-pill" href="#atlas">
          <span className="live-dot" /> Release 0.1
        </a>
      </header>

      <section className="hero" id="top">
        <div className="hero-copy">
          <div className="eyebrow"><span>SPARC audit</span><span>Prototype release</span></div>
          <h1>What does Rubin see that older telescopes did not?</h1>
          <p className="hero-deck">
            A measured atlas of the faint visible matter around nearby galaxies —
            and how it changes the mass and gravity we infer.
          </p>
          <div className="hero-actions">
            <a className="button button-primary" href="#atlas">Explore the atlas <span>↗</span></a>
            <a className="text-link" href="#method">Read the methodology <span>→</span></a>
          </div>
          <p className="prototype-note"><span>Demo</span> Interface values are illustrative until the first validated data release.</p>
        </div>

        <div className={`hero-visual band-${band.toLowerCase()}`}>
          <img src="/rubin-virgo.jpg" alt="Rubin Observatory view of spiral galaxies in the Virgo Cluster" />
          <div className="image-shade" />
          <div className="image-topline">
            <span>VIRGO CLUSTER · RUBIN COMMISSIONING</span>
            <span>RGB COMPOSITE</span>
          </div>
          <div className="target-ring ring-one"><i /></div>
          <div className="target-ring ring-two"><i /></div>
          <div className="discovery-card">
            <span className="discovery-kicker">Diffuse-light view</span>
            <strong>Outer structure, resolved</strong>
            <span>Multi-scale imaging · injection calibrated</span>
          </div>
          <span className="image-credit">NSF–DOE Vera C. Rubin Observatory / NOIRLab</span>
        </div>
      </section>

      <section className="signal-strip" aria-label="Atlas principles">
        <div><strong>6</strong><span>Rubin bands</span></div>
        <div><strong>3,000 deg²</strong><span>EDP2 footprint</span></div>
        <div><strong>FITS + masks</strong><span>Science-ready files</span></div>
        <div><strong>Open method</strong><span>Reproducible provenance</span></div>
      </section>

      <section className="atlas-section" id="atlas">
        <div className="section-heading">
          <div>
            <span className="section-index">01 / THE ATLAS</span>
            <h2>Follow the missing light.</h2>
          </div>
          <p>Select any object to compare its registered survey views, then read each change against an expected audit range.</p>
        </div>

        <div className="workspace">
          <aside className="catalog-panel">
            <div className="search-wrap">
              <span aria-hidden="true">⌕</span>
              <input
                aria-label="Search galaxies"
                placeholder="Search object or feature"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>
            <div className="filter-row" aria-label="Filter by analysis status">
              {["All", "Analyzed", "Review", "Queued"].map((item) => (
                <button
                  key={item}
                  className={status === item ? "active" : ""}
                  onClick={() => setStatus(item)}
                >
                  {item}
                </button>
              ))}
            </div>
            <div className="catalog-meta"><span>{filtered.length} prototype objects</span><span>Sort: confidence ↓</span></div>
            <div className="galaxy-list">
              {filtered.map((galaxy) => (
                <button
                  type="button"
                  className={`galaxy-row ${selectedGalaxy.slug === galaxy.slug ? "selected" : ""}`}
                  onClick={() => selectGalaxy(galaxy.slug)}
                  aria-pressed={selectedGalaxy.slug === galaxy.slug}
                  key={galaxy.slug}
                >
                  <span className="galaxy-thumb" style={{ backgroundPosition: galaxy.crop }} />
                  <span className="galaxy-identity">
                    <strong>{galaxy.name}</strong>
                    <small>{galaxy.catalog} · {galaxy.distance}</small>
                  </span>
                  <span className="galaxy-signal">
                    <strong>{galaxy.diskDelta}</strong>
                    <small>outer radius</small>
                  </span>
                  <span className={`status-mark status-${galaxy.status.toLowerCase()}`} aria-label={galaxy.status} />
                </button>
              ))}
              {filtered.length === 0 && <p className="empty-state">No objects match this view.</p>}
            </div>
          </aside>

          <article className="object-panel" id={selectedGalaxy.slug}>
            <div className="object-head">
              <div>
                <span className="object-id">{selectedGalaxy.catalog} · DEMONSTRATION RECORD</span>
                <h3>{selectedGalaxy.name}</h3>
                <p>{selectedGalaxy.morphology} · {selectedGalaxy.constellation} · {selectedGalaxy.distance}</p>
              </div>
              <Link className="open-record" href={`/galaxy/${selectedGalaxy.slug}`}>Open record <span>↗</span></Link>
            </div>

            <div className="viewer-tabs" role="group" aria-label="Image band">
              {bands.map((item) => (
                <button key={item} className={band === item ? "active" : ""} onClick={() => setBand(item)}>{item}</button>
              ))}
            </div>

            <div className={`comparison-view band-${band.toLowerCase()}`}>
              <div className="comparison-layer rubin-layer">
                <img
                  src="/rubin-virgo.jpg"
                  alt={`${selectedGalaxy.name} Rubin comparison view`}
                  style={{ objectPosition: selectedGalaxy.crop }}
                />
              </div>
              <div
                className="comparison-layer legacy-layer"
                style={{ clipPath: `inset(0 ${100 - reveal}% 0 0)` }}
                aria-hidden="true"
              >
                <img src="/rubin-virgo.jpg" alt="" style={{ objectPosition: selectedGalaxy.crop }} />
                <span>LEGACY</span>
              </div>
              <span className="rubin-label">RUBIN · {band}</span>
              <div className="slider-line" style={{ left: `${reveal}%` }}><i>↔</i></div>
              <input
                className="comparison-range"
                type="range"
                min="12"
                max="88"
                value={reveal}
                onChange={(event) => setReveal(Number(event.target.value))}
                aria-label="Reveal legacy survey comparison"
              />
            </div>
            <div className="viewer-caption">
              <span>Central galaxy · 2.5 × expected disk radius</span>
              <span className="alignment-status"><i /> REGISTERED · PSF MATCHED</span>
              <span>N ↑ · 12.4′ field</span>
            </div>

            <div className="metrics-row">
              <div className="metric-tile">
                <span className={`signal-badge signal-${selectedGalaxy.outerLevel}`}>{signalLabels[selectedGalaxy.outerLevel]}</span>
                <span>Outer radius</span>
                <strong>{selectedGalaxy.diskDelta}</strong>
                <small>{selectedGalaxy.outerContext}</small>
              </div>
              <div className="metric-tile">
                <span className={`signal-badge signal-${selectedGalaxy.gravityLevel}`}>{signalLabels[selectedGalaxy.gravityLevel]}</span>
                <span>Δg<sub>bar</sub> at 15 kpc</span>
                <strong>{selectedGalaxy.gravityDelta}</strong>
                <small>{selectedGalaxy.gravityContext}</small>
              </div>
              <div className="metric-tile">
                <span className={`signal-badge signal-${selectedGalaxy.inclinationLevel}`}>{signalLabels[selectedGalaxy.inclinationLevel]}</span>
                <span>Inclination revision</span>
                <strong>{selectedGalaxy.inclinationDelta}</strong>
                <small>{selectedGalaxy.inclinationContext}</small>
              </div>
              <div className="metric-tile">
                <span className={`signal-badge signal-${selectedGalaxy.structuresLevel}`}>{signalLabels[selectedGalaxy.structuresLevel]}</span>
                <span>New structures</span>
                <strong>{selectedGalaxy.structures}</strong>
                <small>{selectedGalaxy.structuresContext}</small>
              </div>
            </div>
            <div className="metric-key">
              <strong>How to read these</strong>
              <span><i className="key-large" /> Large: clearly beyond the prototype audit range</span>
              <span><i className="key-above" /> Above expected: worth scientific review</span>
              <span><i className="key-expected" /> Within expected: consistent with survey or pipeline differences</span>
            </div>
          </article>
        </div>
      </section>

      <section className="discrepancy-section">
        <div className="section-heading compact">
          <div>
            <span className="section-index">02 / WHAT CHANGED</span>
            <h2>The discrepancy card.</h2>
          </div>
          <p>One legible account of the scientific difference, backed by downloadable measurements.</p>
        </div>
        <div className="discrepancy-grid">
          <article className="quote-card">
            <span className="card-label">{selectedGalaxy.name} · AUTOMATED SUMMARY · PROTOTYPE</span>
            {selectedGalaxy.outerLevel === "pending" ? (
              <blockquote>“This object is queued. Its aligned images are ready, but the light profile and significance tests have <em>not yet been run</em>.”</blockquote>
            ) : (
              <blockquote>
                “Rubin traces the disk <em>{selectedGalaxy.diskDelta} farther</em> than the legacy image. The additional light changes estimated baryonic acceleration by <em>{selectedGalaxy.gravityDelta} at 15 kpc</em>. {selectedGalaxy.structures} outer structure candidates merit review.”
              </blockquote>
            )}
            <div className="confidence-row">
              <span><i style={{ width: `${selectedGalaxy.confidence}%` }} />Detection probability</span><strong>{selectedGalaxy.confidence}%</strong>
              <span><i style={{ width: `${100 - selectedGalaxy.confidence}%` }} />False-positive estimate</span><strong>{100 - selectedGalaxy.confidence}%</strong>
            </div>
          </article>
          <article className="profile-card">
            <div className="profile-head"><span>RADIAL LIGHT PROFILE</span><span>r band · mag arcsec⁻²</span></div>
            <div className="profile-chart" aria-label="Illustrative radial light profile chart">
              <span className="axis y-axis">Fainter ↑</span>
              <span className="axis x-axis">Radius →</span>
              {[25, 50, 75].map((value) => <i className="gridline" style={{ bottom: `${value}%` }} key={value} />)}
              {profileLegacy.map((value, index) => <b className="plot-dot legacy-dot" style={{ left: `${8 + index * 7.7}%`, bottom: `${value}%` }} key={`l-${index}`} />)}
              {profileRubin.map((value, index) => <b className="plot-dot rubin-dot" style={{ left: `${8 + index * 7.7}%`, bottom: `${value}%` }} key={`r-${index}`} />)}
            </div>
            <div className="chart-legend"><span><i className="rubin-key" /> Rubin</span><span><i className="legacy-key" /> Legacy</span><strong>{selectedGalaxy.diskDelta === "Pending" ? "Profile queued" : `Outer-radius change: ${selectedGalaxy.diskDelta}`}</strong></div>
          </article>
        </div>
      </section>

      <section className="method-section" id="method">
        <div className="method-copy">
          <span className="section-index">03 / CREDIBILITY LAYER</span>
          <h2>Every faint feature has to survive the test.</h2>
          <p>Low-surface-brightness work is vulnerable to sky subtraction, scattered light, detector artifacts, and masking choices. We calibrate completeness by injecting known structures into real images and measuring what comes back.</p>
          <a className="text-link light" href="#data">Inspect the published evidence <span>→</span></a>
        </div>
        <div className="method-flow">
          <div><span>01</span><strong>Inject</strong><p>Outer disks, streams, shells, dwarfs, and halos.</p></div>
          <div><span>02</span><strong>Recover</strong><p>Run the same blinded pipeline used on survey data.</p></div>
          <div><span>03</span><strong>Score</strong><p>Publish completeness, bias, and false-positive rates.</p></div>
          <div><span>04</span><strong>Review</strong><p>Compare pipelines and log independent human checks.</p></div>
        </div>
        <div className="method-stamp"><span>INJECTION–RECOVERY</span><strong>Required for every release</strong></div>
      </section>

      <section className="data-section" id="data">
        <div className="data-copy">
          <span className="section-index">04 / OPEN DATA</span>
          <h2>Pretty pictures are only the cover.</h2>
          <p>Every permanent object record ships with calibrated pixels, uncertainty, masks, PSF, provenance, radial measurements, and feature annotations — ready for science or machine learning.</p>
          <div className="data-actions">
            <a className="button button-dark" href="/sample-package.json" download>Download sample JSON ↓</a>
            <Link className="text-link" href="/galaxy/ngc-300">View a full object record →</Link>
          </div>
        </div>
        <div className="package-card">
          <div className="package-head"><span>ngc-300/</span><span>v0.1 · 2.4 GB</span></div>
          <div className="file-tree">
            <span><i>PARQUET</i>metadata.parquet</span>
            <span><i>FITS</i>rubin_g · rubin_r · rubin_i</span>
            <span><i>FITS</i>variance · mask · psf</span>
            <span><i>FITS</i>legacy_optical</span>
            <span><i>FITS</i>segmentation</span>
            <span><i>PARQUET</i>radial_profile.parquet</span>
            <span><i>JSON</i>feature_annotations.json</span>
          </div>
          <div className="package-foot"><span>✓ checksums</span><span>✓ provenance</span><span>✓ DOI-ready</span></div>
        </div>
      </section>

      <section className="closing" id="about">
        <span className="closing-orbit" aria-hidden="true"><i /></span>
        <p>A neutral measurement layer for the visible universe.</p>
        <h2>Measure what changed.<br />Let the evidence travel.</h2>
        <a className="button button-primary" href="#atlas">Enter the atlas <span>↗</span></a>
      </section>

      <footer>
        <Link className="brand footer-brand" href="#top">
          <span className="brand-mark" aria-hidden="true"><i /></span>
          <span>Rubin <strong>Missing Light</strong> Atlas</span>
        </Link>
        <p>Prototype concept · Not affiliated with Rubin Observatory</p>
        <div><a href="#method">Method</a><a href="#data">Data</a><a href="https://noirlab.edu/public/images/noirlab2521ak/">Image credit ↗</a></div>
      </footer>
    </main>
  );
}
