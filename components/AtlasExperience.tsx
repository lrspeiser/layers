"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { Comparison, Layer, LayersCatalog, LayerTarget } from "@/lib/layers";
import { comparisonIsSwipeable, layerStatusLabel } from "@/lib/layers";

type CoverageFilter = "all" | "rubin" | "no-coverage";

function layerTone(layer: Layer) {
  if (layer.availability === "available" || layer.availability === "published") return "ready";
  if (layer.availability === "available-local") return "local";
  if (layer.availability === "metadata-match" || layer.availability === "no-valid-pixels") return "review";
  return "muted";
}

function layerById(target: LayerTarget, id: string) {
  return target.layers.find((layer) => layer.id === id) ?? target.layers[0];
}

function matchingComparison(target: LayerTarget, leftId: string, rightId: string) {
  return target.comparisons.find((item) => item.layerIds.includes(leftId) && item.layerIds.includes(rightId));
}

function LayerBadge({ layer }: { layer: Layer }) {
  return (
    <span className={`layer-badge tone-${layerTone(layer)}`}>
      <i /> {layerStatusLabel(layer)}
    </span>
  );
}

function DataLayerCard({ layer, side }: { layer: Layer; side: "A" | "B" }) {
  return (
    <article className="layer-card">
      <div className="layer-card-top">
        <span className="layer-side">{side}</span>
        <LayerBadge layer={layer} />
      </div>
      <h3>{layer.survey}</h3>
      <p>{layer.release} · {layer.instrument}</p>
      <div className="layer-specs">
        <span><small>TYPE</small>{layer.kind}</span>
        <span><small>VIEW</small>{layer.renderMode}</span>
        <span><small>BANDS</small>{layer.bands.length ? layer.bands.join(" · ") : "—"}</span>
        <span><small>DATASETS</small>{layer.datasetCount ?? "—"}</span>
      </div>
    </article>
  );
}

function LayerViewport({ target, left, right }: { target: LayerTarget; left: Layer; right: Layer }) {
  const [reveal, setReveal] = useState(50);
  const comparison = matchingComparison(target, left.id, right.id);
  const swipeable = Boolean(comparison && comparisonIsSwipeable(comparison, target.layers));

  if (swipeable && comparison) {
    return (
      <div className="layers-viewport image-viewport">
        <img src={right.assets?.preview} alt={`${target.name}, ${right.survey}`} />
        <div className="reveal-layer" style={{ clipPath: `inset(0 ${100 - reveal}% 0 0)` }}>
          <img src={left.assets?.preview} alt={`${target.name}, ${left.survey}`} />
        </div>
        <span className="viewport-label label-left">{left.survey}</span>
        <span className="viewport-label label-right">{right.survey}</span>
        <span className="reveal-rule" style={{ left: `${reveal}%` }}><i>↔</i></span>
        <input
          type="range"
          min="3"
          max="97"
          value={reveal}
          onChange={(event) => setReveal(Number(event.target.value))}
          aria-label={`Reveal ${left.survey} over ${right.survey}`}
        />
      </div>
    );
  }

  const noValidPixels = [left, right].find((layer) => layer.availability === "no-valid-pixels");
  const bothImages = left.kind === "image" && right.kind === "image";
  return (
    <div className="layers-viewport blocked-viewport">
      <div className="sky-grid" aria-hidden="true"><i /><i /><i /><i /></div>
      <div className="target-reticle" aria-hidden="true"><i /><b /></div>
      <div className="viewport-message">
        <span className="eyebrow">{noValidPixels ? "FOOTPRINT AUDIT" : bothImages ? "COMPARISON GATE" : "MIXED DATA TYPES"}</span>
        <h3>{noValidPixels ? "A metadata match is not usable sky coverage." : bothImages ? "Swipe view waits for matched, publishable pixels." : "These layers need different views."}</h3>
        <p>
          {noValidPixels
            ? noValidPixels.note
            : bothImages
              ? "Both image layers must share a sky grid and footprint, with PSF, units, masks, and background reconciled. Until then, Layers shows their evidence without implying a pixel difference."
              : `${left.survey} is a ${left.kind} layer and ${right.survey} is a ${right.kind} layer. Layers will combine them as an image plus a linked ${left.kind === "profile" || right.kind === "profile" ? "radial plot" : "scientific view"}, not as two fake pictures.`}
        </p>
        <div className="coordinate-strip">
          <span><small>RA</small>{target.center.raDeg.toFixed(5)}°</span>
          <span><small>DEC</small>{target.center.decDeg.toFixed(5)}°</span>
          <span><small>FIELD</small>{target.region.widthArcmin}′</span>
          <span><small>FRAME</small>{target.center.frame}</span>
        </div>
      </div>
      <div className="gate-checks">
        <span className="gate-pass"><i>✓</i> target identity</span>
        <span className={left.availability !== "not-covered" ? "gate-pass" : ""}><i>{left.availability !== "not-covered" ? "✓" : "2"}</i> layer A</span>
        <span className={right.availability !== "not-covered" ? "gate-pass" : ""}><i>{right.availability !== "not-covered" ? "✓" : "3"}</i> layer B</span>
        <span><i>4</i> registration + QA</span>
      </div>
    </div>
  );
}

function DifferencePanel({ comparison }: { comparison?: Comparison }) {
  if (!comparison || comparison.status !== "published") {
    return (
      <section className="difference-panel">
        <div className="panel-heading">
          <span className="eyebrow">WHAT CHANGED?</span>
          <span className="claim-state">NO CLAIM YET</span>
        </div>
        <h3>Measurements stay blank until the comparison is valid.</h3>
        <p>Layers will publish the change, statistical and systematic uncertainty, expected cross-survey range, significance, provenance, and caveats together.</p>
        <div className="difference-scale">
          <span><i className="expected" /> Expected <small>&lt;2σ</small></span>
          <span><i className="noteworthy" /> Noteworthy <small>2–3σ</small></span>
          <span><i className="large" /> Large <small>≥3σ</small></span>
        </div>
      </section>
    );
  }

  return (
    <section className="difference-panel">
      <div className="panel-heading"><span className="eyebrow">WHAT CHANGED?</span><span className="claim-state published">QA PASSED</span></div>
      <div className="measurement-grid">
        {comparison.measurements.map((measurement) => (
          <article key={measurement.id}>
            <span>{measurement.classification}</span>
            <h3>{measurement.label}</h3>
            <strong>{measurement.value} {measurement.unit}</strong>
            <p>±{measurement.statisticalUncertainty} stat · ±{measurement.systematicUncertainty} sys · {measurement.significanceSigma.toFixed(1)}σ</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function AssumptionPanel({ target, comparison }: { target: LayerTarget; comparison?: Comparison }) {
  const falseFootprint = target.layers.some((layer) => layer.availability === "no-valid-pixels");
  return (
    <section className="assumption-panel">
      <div className="panel-heading"><span className="eyebrow">ASSUMPTIONS WORTH RECHECKING</span><span className="triage-only">TRIAGE, NOT A VERDICT</span></div>
      {comparison?.assumptionAudits.length ? comparison.assumptionAudits.map((audit) => (
        <article key={audit.id}><h3>{audit.title}</h3><p>{audit.newEvidence}</p><strong>{audit.confidence}</strong></article>
      )) : (
        <div className="assumption-empty">
          <span className="assumption-number">{falseFootprint ? "01" : "—"}</span>
          <div>
            <h3>{falseFootprint ? "Survey footprint means science pixels exist here." : "No scientific assumption is flagged yet."}</h3>
            <p>{falseFootprint ? "The SIA polygon intersected this field, but every intersecting pixel was NO_DATA. Coverage must be validated at pixel level before a target enters an analysis sample." : "Candidate audits for baryonic mass, morphology, lensing, distance, or source counts appear only after a measured difference and systematic checks exist."}</p>
          </div>
        </div>
      )}
    </section>
  );
}

export function AtlasExperience({ catalog }: { catalog: LayersCatalog }) {
  const firstUseful = catalog.targets.find((target) => target.layers.some((layer) => layer.availability === "available-local")) ?? catalog.targets[0];
  const [query, setQuery] = useState("");
  const [coverageFilter, setCoverageFilter] = useState<CoverageFilter>("rubin");
  const [selectedId, setSelectedId] = useState(firstUseful.id);
  const selected = catalog.targets.find((target) => target.id === selectedId) ?? firstUseful;
  const [leftId, setLeftId] = useState(selected.layers[0].id);
  const [rightId, setRightId] = useState(selected.layers[1]?.id ?? selected.layers[0].id);

  const filtered = useMemo(() => catalog.targets.filter((target) => {
    const text = `${target.name} ${Object.values(target.identifiers).join(" ")}`.toLowerCase();
    if (!text.includes(query.toLowerCase())) return false;
    const rubin = target.layers.find((layer) => layer.id === "rubin-dp2-deep-coadd");
    if (coverageFilter === "rubin") return rubin?.availability !== "not-covered";
    if (coverageFilter === "no-coverage") return rubin?.availability === "not-covered";
    return true;
  }), [catalog.targets, coverageFilter, query]);

  const chooseTarget = (target: LayerTarget) => {
    setSelectedId(target.id);
    setLeftId(target.layers[0].id);
    setRightId(target.layers[1]?.id ?? target.layers[0].id);
  };
  const left = layerById(selected, leftId);
  const right = layerById(selected, rightId);
  const comparison = matchingComparison(selected, left.id, right.id);

  return (
    <main id="top">
      <header className="layers-header">
        <Link className="layers-brand" href="#top"><span className="brand-glyph"><i /><b /></span><strong>Layers</strong><small>science comparison workspace</small></Link>
        <nav><a href="#workspace">Workspace</a><a href="#method">Method</a><a href="/api/catalog">API</a></nav>
        <span className="release-chip">SPARC × RUBIN DP2 · PILOT</span>
      </header>

      <section className="release-bar">
        <div><span className="eyebrow">CURRENT RELEASE</span><h1>See the same sky through every credible layer.</h1><p>Align observations, measure what changed, and find assumptions worth rechecking—without confusing a difference with a discovery.</p></div>
        <div className="release-stats">
          <span><strong>{catalog.summary.targets}</strong><small>targets audited</small></span>
          <span><strong>{catalog.summary.rubinSiaMatches}</strong><small>Rubin footprint matches</small></span>
          <span><strong>{catalog.summary.rubinUsableLocal}</strong><small>usable local mosaics</small></span>
          <span><strong>{catalog.summary.publishedComparisons}</strong><small>published comparisons</small></span>
        </div>
      </section>

      <section className="layers-workspace" id="workspace">
        <aside className="target-browser">
          <div className="browser-title"><span className="eyebrow">TARGETS</span><strong>{catalog.targetSelection.name}</strong></div>
          <label className="target-search"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Name or identifier" aria-label="Search targets" /></label>
          <div className="filter-tabs" role="group" aria-label="Coverage filter">
            {(["rubin", "all", "no-coverage"] as CoverageFilter[]).map((item) => <button key={item} className={coverageFilter === item ? "active" : ""} onClick={() => setCoverageFilter(item)}>{item === "rubin" ? "Rubin matches" : item === "all" ? "All 175" : "No coverage"}</button>)}
          </div>
          <div className="target-count">{filtered.length} targets</div>
          <div className="target-list">
            {filtered.map((target) => {
              const rubin = target.layers.find((layer) => layer.id === "rubin-dp2-deep-coadd")!;
              return <button key={target.id} className={target.id === selected.id ? "selected" : ""} onClick={() => chooseTarget(target)}>
                <span className="target-dot" />
                <span><strong>{target.name}</strong><small>{target.center.raDeg.toFixed(3)}° · {target.center.decDeg.toFixed(3)}°</small></span>
                <em className={`tone-${layerTone(rubin)}`}>{layerStatusLabel(rubin)}</em>
              </button>;
            })}
          </div>
        </aside>

        <section className="comparison-workbench">
          <div className="target-heading">
            <div><span className="eyebrow">{selected.selection.sample} · TARGET RECORD</span><h2>{selected.name}</h2><p>{selected.identifiers.SIMBAD} · RA {selected.center.raDeg.toFixed(5)}° · Dec {selected.center.decDeg.toFixed(5)}°</p></div>
            <Link href={`/target/${selected.id}`}>Permanent record ↗</Link>
          </div>

          <div className="layer-selectors">
            <label><span>LAYER A</span><select value={left.id} onChange={(event) => setLeftId(event.target.value)}>{selected.layers.map((layer) => <option value={layer.id} key={layer.id}>{layer.survey} · {layer.release}</option>)}</select></label>
            <span className="versus">COMPARE</span>
            <label><span>LAYER B</span><select value={right.id} onChange={(event) => setRightId(event.target.value)}>{selected.layers.map((layer) => <option value={layer.id} key={layer.id}>{layer.survey} · {layer.release}</option>)}</select></label>
          </div>

          <div className="layer-cards"><DataLayerCard layer={left} side="A" /><DataLayerCard layer={right} side="B" /></div>
          <div className="view-tabs"><button className="active">Evidence</button><button disabled={!comparisonIsSwipeable(comparison ?? { id: "", layerIds: [left.id, right.id], status: "blocked", measurements: [], inferences: [], assumptionAudits: [] }, selected.layers)}>Swipe</button><button disabled>Overlay</button><button>Profiles</button><span>Views activate by data type + QA</span></div>
          <LayerViewport target={selected} left={left} right={right} />
          <div className="analysis-grid"><DifferencePanel comparison={comparison} /><AssumptionPanel target={selected} comparison={comparison} /></div>
        </section>
      </section>

      <section className="method-band" id="method">
        <div><span className="eyebrow">THE SCIENCE CONTRACT</span><h2>Comparable before compared.</h2><p>Every published result retains original data, provenance, registration, uncertainty, systematic alternatives, and enough information to reproduce the claim.</p></div>
        <ol><li><span>01</span><strong>Locate</strong><p>Find layers covering the declared sky region.</p></li><li><span>02</span><strong>Reconcile</strong><p>Match WCS, footprint, PSF, units, masks, and sky.</p></li><li><span>03</span><strong>Measure</strong><p>Propagate statistical and systematic uncertainty.</p></li><li><span>04</span><strong>Audit</strong><p>Separate observation, inference, and speculation.</p></li></ol>
      </section>

      <footer><Link className="layers-brand" href="#top"><span className="brand-glyph"><i /><b /></span><strong>Layers</strong></Link><p>Independent scientific prototype · No fabricated pixels or claims.</p><div><a href="https://github.com/lrspeiser/rubin-light-atlas">Source ↗</a><a href="/api/catalog">Catalog API</a></div></footer>
    </main>
  );
}
