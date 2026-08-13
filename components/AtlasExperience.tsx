"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import type { Comparison, Layer, LayersCatalog, LayerTarget, SparcProfile } from "@/lib/layers";
import { comparisonIsSwipeable, layerStatusLabel } from "@/lib/layers";
import comparisonPreviewData from "@/public/data/comparison-previews.json";

type CoverageFilter = "all" | "rubin" | "wise-mass" | "no-coverage";
type WorkbenchView = "evidence" | "swipe" | "coverage" | "candidates" | "profiles";
type CandidateRegion = {
  id: string;
  xPercent: number;
  yPercent: number;
  pixelCount: number;
  peakEmpiricalSigma: number;
  direction: string;
};
type PrototypeScience = {
  candidateRegions: CandidateRegion[];
  differenceMethod: { thresholdEmpiricalSigma: number };
};
type ComparisonPreview = (typeof comparisonPreviewData.comparisons)[number];

function matchingPreview(targetId: string, comparison?: Comparison) {
  if (!comparison) return undefined;
  return comparisonPreviewData.comparisons.find((preview) =>
    preview.objectId === targetId && preview.layerIds.every((id) => comparison.layerIds.includes(id)),
  ) as ComparisonPreview | undefined;
}

function linePath(points: Array<{ x: number; y: number }>) {
  return points.map((point, index) => `${index ? "L" : "M"}${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ");
}

function ProfileChart({ profile, kind }: { profile: SparcProfile; kind: "photometry" | "rotation" }) {
  const width = 680;
  const height = 280;
  const padding = { left: 54, right: 18, top: 18, bottom: 42 };
  if (kind === "photometry") {
    const data = profile.surfaceBrightness;
    const maxX = Math.max(...data.map((point) => point.radiusArcsec));
    const minY = Math.floor(Math.min(...data.map((point) => point.surfaceBrightnessMagArcsec2)));
    const maxY = Math.ceil(Math.max(...data.map((point) => point.surfaceBrightnessMagArcsec2)));
    const points = data.map((point) => ({
      x: padding.left + point.radiusArcsec / maxX * (width - padding.left - padding.right),
      y: padding.top + (point.surfaceBrightnessMagArcsec2 - minY) / Math.max(maxY - minY, 1) * (height - padding.top - padding.bottom),
      source: point,
    }));
    return <div className="profile-chart"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`SPARC 3.6 micron surface brightness for ${profile.sparcId}`}>
      <path className="chart-axis" d={`M${padding.left},${padding.top}V${height - padding.bottom}H${width - padding.right}`} />
      <path className="profile-line photometry-line" d={linePath(points)} />
      {points.map((point, index) => <circle key={index} className={point.source.accepted ? "accepted-point" : "rejected-point"} cx={point.x} cy={point.y} r={point.source.accepted ? 2.7 : 2.1}><title>{point.source.radiusArcsec.toFixed(1)} arcsec: {point.source.surfaceBrightnessMagArcsec2.toFixed(2)}{point.source.uncertaintyMag === null ? "" : ` ± ${point.source.uncertaintyMag.toFixed(2)}`} mag/arcsec²</title></circle>)}
      <text className="chart-label" x={width / 2} y={height - 8}>Radius (arcsec)</text>
      <text className="chart-label" transform={`translate(13 ${height / 2}) rotate(-90)`}>3.6 µm surface brightness (mag/arcsec²)</text>
      <text className="chart-tick" x={padding.left} y={height - 24}>0</text><text className="chart-tick" x={width - padding.right} y={height - 24} textAnchor="end">{maxX.toFixed(0)}</text>
      <text className="chart-tick" x={padding.left - 8} y={padding.top + 4} textAnchor="end">{minY}</text><text className="chart-tick" x={padding.left - 8} y={height - padding.bottom} textAnchor="end">{maxY}</text>
    </svg><span className="chart-orientation">Brighter ↑</span></div>;
  }
  const data = profile.rotationCurve;
  const maxX = Math.max(...data.map((point) => point.radiusKpc));
  const maxY = Math.ceil(Math.max(...data.map((point) => point.observedVelocityKmS + point.velocityUncertaintyKmS)) / 10) * 10;
  const toPoints = (accessor: (point: SparcProfile["rotationCurve"][number]) => number) => data.map((point) => ({
    x: padding.left + point.radiusKpc / maxX * (width - padding.left - padding.right),
    y: height - padding.bottom - accessor(point) / Math.max(maxY, 1) * (height - padding.top - padding.bottom),
  }));
  const observed = toPoints((point) => point.observedVelocityKmS);
  return <div className="profile-chart"><svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`SPARC rotation curve for ${profile.sparcId}`}>
    <path className="chart-axis" d={`M${padding.left},${padding.top}V${height - padding.bottom}H${width - padding.right}`} />
    <path className="profile-line gas-line" d={linePath(toPoints((point) => Math.abs(point.gasVelocityKmS)))} />
    <path className="profile-line disk-line" d={linePath(toPoints((point) => Math.abs(point.diskVelocityKmS)))} />
    <path className="profile-line rotation-line" d={linePath(observed)} />
    {observed.map((point, index) => <circle key={index} className="rotation-point" cx={point.x} cy={point.y} r="3"><title>{data[index].radiusKpc.toFixed(2)} kpc: {data[index].observedVelocityKmS.toFixed(1)} ± {data[index].velocityUncertaintyKmS.toFixed(1)} km/s</title></circle>)}
    <text className="chart-label" x={width / 2} y={height - 8}>Radius (kpc)</text><text className="chart-label" transform={`translate(13 ${height / 2}) rotate(-90)`}>Circular speed (km/s)</text>
    <text className="chart-tick" x={padding.left} y={height - 24}>0</text><text className="chart-tick" x={width - padding.right} y={height - 24} textAnchor="end">{maxX.toFixed(1)}</text><text className="chart-tick" x={padding.left - 8} y={padding.top + 4} textAnchor="end">{maxY}</text>
  </svg><div className="chart-series"><span className="observed-key">Observed</span><span className="gas-key">Gas</span><span className="disk-key">Stellar disk</span></div></div>;
}

function MassComparisonCard({ comparison }: { comparison: Comparison }) {
  const values = comparison.catalogValues;
  const measurement = comparison.measurements[0];
  if (!values || !measurement) return null;
  const minimum = Math.floor(Math.min(values.wiseLogStellarMassMsun, values.sparcBaselineLogStellarMassMsun) - 0.25);
  const maximum = Math.ceil(Math.max(values.wiseLogStellarMassMsun, values.sparcBaselineLogStellarMassMsun) + 0.25);
  const width = (value: number) => `${Math.max(4, (value - minimum) / Math.max(maximum - minimum, 1) * 100)}%`;
  return <section className="mass-comparison-card">
    <div className="mass-card-heading"><div><span className="eyebrow">LINKED CATALOG COMPARISON</span><h3>Total stellar-mass normalization</h3></div><span className={`mass-classification ${measurement.classification}`}>{measurement.classification} · {measurement.significanceSigma.toFixed(2)}σ</span></div>
    <div className="mass-bars">
      <div><span><strong>WISE W1 color model</strong><small>{values.wiseLogStellarMassMsun.toFixed(3)} ± {values.wiseStatisticalUncertaintyDex.toFixed(3)} dex</small></span><i style={{ width: width(values.wiseLogStellarMassMsun) }} /></div>
      <div><span><strong>SPARC 3.6 µm baseline</strong><small>{values.sparcBaselineLogStellarMassMsun.toFixed(3)} dex · fixed M/L 0.5</small></span><i style={{ width: width(values.sparcBaselineLogStellarMassMsun) }} /></div>
    </div>
    <div className="mass-difference-readout"><span><small>OBSERVED MODEL DIFFERENCE</small><strong>{measurement.value > 0 ? "+" : ""}{measurement.value.toFixed(3)} dex</strong></span><span><small>EXPECTED W1 OFFSET</small><strong>+{measurement.expectedCenter?.toFixed(2)} ± {measurement.systematicUncertainty.toFixed(2)} dex</strong></span></div>
    <p>The bars compare published target-level model outputs. They do not imply a radial mass map or a Rubin measurement.</p>
  </section>;
}

function SparcProfileViewport({ target, profile, companionLayer, comparison }: { target: LayerTarget; profile?: SparcProfile; companionLayer?: Layer; comparison?: Comparison }) {
  const [chart, setChart] = useState<"photometry" | "rotation">("photometry");
  if (!profile) return <div className="layers-viewport blocked-viewport"><div className="viewport-message"><span className="eyebrow">PROFILE DATA UNAVAILABLE</span><h3>No SPARC record was loaded for {target.name}.</h3></div></div>;
  return <div className="layers-viewport sparc-profile-viewport">
    <div className="profile-view-heading"><div><span className="eyebrow">PUBLISHED NON-IMAGE LAYER</span><h3>{profile.sparcId} · SPARC 2016</h3><p>Radial photometry and dynamical measurements retain their physical axes; they are linked to the same target rather than converted into fake pixels.</p></div><div className="profile-view-tabs"><button className={chart === "photometry" ? "active" : ""} onClick={() => setChart("photometry")}>Surface brightness</button><button className={chart === "rotation" ? "active" : ""} onClick={() => setChart("rotation")}>Rotation curve</button></div></div>
    <div className={companionLayer?.assets?.preview ? "linked-profile-layout" : undefined}>
      {companionLayer?.assets?.preview && <figure className="linked-layer-image"><img src={companionLayer.assets.preview} alt={`${target.name}, ${companionLayer.survey} ${companionLayer.bands.join(" ")}`} /><figcaption><strong>{companionLayer.survey} · {companionLayer.bands.join(" ")}</strong><span>Authentic image layer · display stretch</span><small>{companionLayer.note}</small></figcaption></figure>}
      <ProfileChart profile={profile} kind={chart} />
    </div>
    {companionLayer?.kind === "catalog" && comparison?.comparisonMode === "catalog-profile" && <MassComparisonCard comparison={comparison} />}
    <div className="profile-facts"><span><small>DISTANCE</small><strong>{profile.distanceMpc?.toFixed(1) ?? "—"} Mpc</strong></span><span><small>ACCEPTED PHOTOMETRY</small><strong>{profile.summary.acceptedPhotometryPoints} points</strong></span><span><small>ACCEPTED EXTENT</small><strong>{profile.summary.maximumAcceptedRadiusArcsec.toFixed(1)}″</strong></span><span><small>ROTATION CURVE</small><strong>{profile.summary.rotationCurvePoints} points · {profile.summary.maximumRotationRadiusKpc.toFixed(1)} kpc</strong></span></div>
    <div className="profile-provenance"><span>Source: Lelli, McGaugh & Schombert (2016), AJ 152, 157</span><a href="https://astroweb.cwru.edu/SPARC/" target="_blank" rel="noreferrer">Official SPARC archive ↗</a></div>
  </div>;
}

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

function defaultLayerIds(target: LayerTarget): [string, string] {
  const audited = target.comparisons.find((comparison) => comparison.status === "published" || comparison.status === "qa");
  if (audited) return audited.layerIds;
  const images = target.layers.filter((layer) => layer.kind === "image" && layer.availability !== "not-covered");
  if (images.length >= 2) return [images[0].id, images[1].id];
  return [target.layers[0].id, target.layers[1]?.id ?? target.layers[0].id];
}

function LayerBadge({ layer }: { layer: Layer }) {
  return (
    <span className={`layer-badge tone-${layerTone(layer)}`}>
      <i /> {layerStatusLabel(layer)}
    </span>
  );
}

function DataLayerCard({ layer, side }: { layer: Layer; side: "A" | "B" }) {
  const coverageValues = Object.values(layer.bandCoverage ?? {});
  const hasBandCoverage = coverageValues.length > 0;
  const validArea = hasBandCoverage
    ? `${Math.round(Math.max(...coverageValues) * 100)}% max`
    : layer.renderMode;
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
        <span><small>{hasBandCoverage ? "VALID AREA" : "VIEW"}</small>{validArea}</span>
        <span><small>BANDS</small>{layer.bands.length ? layer.bands.join(" · ") : "—"}</span>
        <span><small>DATASETS</small>{layer.datasetCount ?? "—"}</span>
      </div>
    </article>
  );
}

function LayerViewport({ target, left, right, view, science, profile }: { target: LayerTarget; left: Layer; right: Layer; view: WorkbenchView; science: PrototypeScience; profile?: SparcProfile }) {
  const [reveal, setReveal] = useState(50);
  const [selectedCandidate, setSelectedCandidate] = useState<CandidateRegion | null>(null);
  const [pixelsAvailable, setPixelsAvailable] = useState(true);
  const comparison = matchingComparison(target, left.id, right.id);
  const swipeable = Boolean(comparison && comparisonIsSwipeable(comparison, target.layers));
  const preview = matchingPreview(target.id, comparison);
  const authenticQaViewer = Boolean(preview && comparison?.qa?.astrometryPass);
  const hasCandidateOverlay = target.id === "ugc00191";
  const registration = comparison?.registration;
  const astrometryPass = Boolean(
    registration?.maxResidualArcsec !== undefined
    && registration.qaThresholdArcsec !== undefined
    && registration.maxResidualArcsec <= registration.qaThresholdArcsec,
  );

  if (view === "profiles" && (left.kind === "profile" || right.kind === "profile")) {
    const companionLayer = left.kind === "profile" ? right : left;
    return <SparcProfileViewport target={target} profile={profile} companionLayer={companionLayer} comparison={comparison} />;
  }

  if ((swipeable && comparison) || authenticQaViewer) {
    const showSwipe = view === "evidence" || view === "swipe";
    const leftIsRubin = left.id === "rubin-dp2-deep-coadd";
    const leftImage = preview ? (leftIsRubin ? preview.assets.rubin.path : preview.assets.reference.path) : left.assets?.preview;
    const rightImage = preview ? (leftIsRubin ? preview.assets.reference.path : preview.assets.rubin.path) : right.assets?.preview;
    return (
      <div className="layers-viewport image-viewport qa-image-viewport">
        {pixelsAvailable ? <>
          <img src={rightImage} alt={`${target.name}, ${right.survey}`} onError={() => setPixelsAvailable(false)} />
          <div className="reveal-layer" style={showSwipe ? { clipPath: `inset(0 ${100 - reveal}% 0 0)` } : undefined}>
            <img src={leftImage} alt={`${target.name}, ${left.survey}`} onError={() => setPixelsAvailable(false)} />
          </div>
          {view === "coverage" && preview && <img className="main-analysis-overlay" src={preview.assets.commonCoverage.path} alt="Red marks Rubin-only coverage, blue reference-only coverage, and amber neither usable" />}
          {view === "candidates" && hasCandidateOverlay && <><img className="main-analysis-overlay" src="/private-preview/ugc00191/candidate-difference.png" alt="Red Rubin-excess and blue Legacy-excess empirical QA candidates" />{science.candidateRegions.map((candidate, index) => <button className={`main-difference-pin direction-${candidate.direction}`} key={candidate.id} style={{ left: `${candidate.xPercent}%`, top: `${candidate.yPercent}%` }} onClick={() => setSelectedCandidate(candidate)} aria-label={`Inspect candidate ${index + 1}`}>{index + 1}</button>)}</>}
        </> : <div className="qa-pixel-fallback"><span className="eyebrow">AUTHENTIC PIXELS REQUIRE DATA ACCESS</span><h3>The interactive controls are ready; this public host does not redistribute the protected DP2 cutout.</h3><p>Open the private Layers deployment or run the repository locally with the downloaded Rubin and Legacy assets.</p><Link href="/prototype">Open the documented real-pixel analysis</Link></div>}
        {showSwipe && <><span className="viewport-label label-left">{left.survey}</span>
        <span className="viewport-label label-right">{right.survey}</span>
        <span className="reveal-rule" style={{ left: `${reveal}%` }}><i>↔</i></span>
        <input
          type="range"
          min="0"
          max="100"
          value={reveal}
          onChange={(event) => setReveal(Number(event.target.value))}
          aria-label={`Reveal ${left.survey} over ${right.survey}`}
        /></>}
        {view === "coverage" && preview && <div className="main-overlay-legend"><strong>PER-LAYER COVERAGE</strong><span className="record-mask-key">Red Rubin only · blue other survey only · amber neither</span><small>{(preview.commonValidPixelFraction * 100).toFixed(1)}% shared analysis area · not brightness</small></div>}
        {view === "candidates" && <div className="main-overlay-legend"><strong>SIGNAL CANDIDATES</strong><span className="legend-red">Rubin excess</span><span className="legend-blue">Legacy excess</span><small>≥{science.differenceMethod.thresholdEmpiricalSigma}σ-like QA residual · not a discovery</small></div>}
        {selectedCandidate && view === "candidates" && <div className="main-candidate-card"><button onClick={() => setSelectedCandidate(null)}>×</button><strong>{selectedCandidate.direction === "rubin-excess" ? "RUBIN-EXCESS" : "LEGACY-EXCESS"} CANDIDATE</strong><span>{Math.abs(selectedCandidate.peakEmpiricalSigma) > 99 ? ">99" : Math.abs(selectedCandidate.peakEmpiricalSigma).toFixed(1)}σ-like peak · {selectedCandidate.pixelCount} pixels</span><p>Inspect this region; variable sources, PSF residuals, filter response, masking, or diffuse light can cause it.</p></div>}
        {authenticQaViewer && <span className="qa-viewer-status">AUTHENTIC MATCHED PIXELS · {preview?.band.toUpperCase()} BAND · QA VIEW · SCIENCE CLAIM BLOCKED</span>}
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
              ? "Both image layers must share a sky grid and footprint, with PSF, filter response, units, masks, and background reconciled. Until then, Layers shows their evidence without implying a pixel difference."
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
        <span className={registration?.commonWcs && registration.commonFootprint ? "gate-pass" : ""}><i>{registration?.commonWcs && registration.commonFootprint ? "✓" : "2"}</i> common grid</span>
        <span className={astrometryPass ? "gate-pass" : ""}><i>{astrometryPass ? "✓" : "3"}</i> astrometry</span>
        <span className={registration?.psfMatched && registration.skyMatched && registration.filterMatched ? "gate-pass" : ""}><i>{registration?.psfMatched && registration.skyMatched && registration.filterMatched ? "✓" : "4"}</i> PSF + filter + sky</span>
      </div>
    </div>
  );
}

function DifferencePanel({ comparison }: { comparison?: Comparison }) {
  if (comparison?.status === "qa" && comparison.qa && comparison.registration) {
    const residual = comparison.qa.astrometricResidualP95Arcsec;
    const threshold = comparison.registration.qaThresholdArcsec;
    const representativeLimits = comparison.measurements.filter((measurement) =>
      measurement.quantity === "central surface brightness recovery limit" && measurement.id.endsWith("re-24")
    );
    return (
      <section className="difference-panel">
        <div className="panel-heading">
          <span className="eyebrow">REGISTRATION QA</span>
          <span className={`claim-state ${comparison.qa.astrometryPass ? "qa-pass" : "qa-fail"}`}>{comparison.qa.astrometryPass ? "ASTROMETRY PASSED" : "ASTROMETRY BLOCKED"}</span>
        </div>
        <h3>{comparison.qa.comparisonLayerLabel} · {comparison.qa.band}-band</h3>
        <p>{comparison.registration.psfMatched && comparison.registration.skyMatched
          ? comparison.qa.injectionRecoveryStatus === "pass"
            ? comparison.qa.extendedSourceTransferStatus === "qa-failed"
              ? `The matched pair passes astrometry, PSF/sky reconciliation, point-source color calibration, and diffuse recovery. Its resolved-galaxy color transfer failed (${comparison.qa.extendedSourceResolvedCells ?? "—"} cells; median |residual| ${comparison.qa.extendedSourceMedianAbsoluteResidualMag?.toFixed(2) ?? "—"} mag), so missing-light and mass claims remain blocked.`
              : "The matched pair now has empirical diffuse-source recovery limits and null tests. Extended-source filter transfer still blocks a Rubin-minus-reference missing-light claim."
            : "A matched FITS pair now shares the sky grid, physical units, PSF target, masks, and sky subtraction. Filter response and injection/recovery still block a scientific difference claim."
          : "This is data-readiness evidence, not a scientific difference claim. PSF, filter response, and sky matching are still unapplied."}</p>
        <div className="qa-readout">
          <span><small>P95 RESIDUAL</small><strong>{residual?.toFixed(3) ?? "—"}″</strong><em>limit {threshold?.toFixed(2) ?? "—"}″</em></span>
          <span><small>COMMON VALID AREA</small><strong>{comparison.qa.commonValidPixelFraction !== undefined ? `${(comparison.qa.commonValidPixelFraction * 100).toFixed(1)}%` : "—"}</strong><em>after masks</em></span>
          <span><small>MATCHED SOURCES</small><strong>{comparison.qa.matchedSources ?? "—"}</strong><em>QA sample</em></span>
        </div>
        {representativeLimits.length > 0 && <div className="recovery-readout">
          <div><small>INJECTION / RECOVERY</small><strong>PASS</strong><em>smooth exponentials · empirical nulls</em></div>
          {representativeLimits.map((measurement) => <div key={measurement.id}><small>{measurement.id.startsWith("rubin") ? "RUBIN" : "REFERENCE"} · 24″ Re</small><strong>{measurement.value.toFixed(1)} mag/arcsec²</strong><em>90% recovered · discrete grid</em></div>)}
        </div>}
        {comparison.qa.extendedSourceTransferStatus === "qa-failed" && <div className="filter-failure-readout"><small>RESOLVED FILTER TRANSFER</small><strong>QA FAILED</strong><span>{comparison.qa.extendedSourceResolvedCells ?? "—"} resolved cells · median |residual| {comparison.qa.extendedSourceMedianAbsoluteResidualMag?.toFixed(2) ?? "—"} mag · robust scatter {comparison.qa.extendedSourceRobustScatterMag?.toFixed(2) ?? "—"} mag</span></div>}
      </section>
    );
  }
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
        <article className="assumption-audit" key={audit.id}>
          <div className="assumption-audit-rank"><span>#{String(audit.rank).padStart(2, "0")}</span><small>{audit.confidence} follow-up candidate</small></div>
          <h3>{audit.title}</h3>
          <p>{audit.newEvidence}</p>
          <div className="assumption-evidence"><strong>{audit.evidenceMagnitude.thresholdMultiple.toFixed(1)}×</strong><span>over the {audit.evidenceMagnitude.passThreshold.toFixed(2)} {audit.evidenceMagnitude.unit} pass threshold</span></div>
          <p className="affected-inference"><strong>Inference gate:</strong> {audit.affectedInference}</p>
          {audit.independentCheck && <div className="independent-check"><small>INDEPENDENT CHECK · {audit.independentCheck.survey}</small><strong>{audit.independentCheck.qualifiedForArbitration ? "READY" : audit.independentCheck.registrationPass ? "CALIBRATION BLOCKED" : "REGISTRATION BLOCKED"}</strong><span>{audit.independentCheck.registrationP95Arcsec?.toFixed(3) ?? "—"}″ p95 · limit {audit.independentCheck.passThresholdArcsec?.toFixed(2) ?? "—"}″</span><p>{audit.independentCheck.note}</p></div>}
          <details><summary>Systematics + recommended follow-up</summary><ul>{audit.systematicAlternatives.map((item) => <li key={item}>{item}</li>)}</ul><ol>{audit.recommendedFollowUp.map((item) => <li key={item}>{item}</li>)}</ol></details>
          <small className="assumption-caveat">{audit.caveat}</small>
        </article>
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

export function AtlasExperience({ catalog, prototypeScience }: { catalog: LayersCatalog; prototypeScience: PrototypeScience }) {
  const firstUseful = catalog.targets.find((target) => target.id === "ugc00191")
    ?? catalog.targets.find((target) => target.layers.some((layer) => layer.availability === "available-local"))
    ?? catalog.targets[0];
  const [query, setQuery] = useState("");
  const [coverageFilter, setCoverageFilter] = useState<CoverageFilter>("rubin");
  const [selectedId, setSelectedId] = useState(firstUseful.id);
  const selected = catalog.targets.find((target) => target.id === selectedId) ?? firstUseful;
  const initialLayers = defaultLayerIds(firstUseful);
  const [leftId, setLeftId] = useState(initialLayers[0]);
  const [rightId, setRightId] = useState(initialLayers[1]);
  const [workbenchView, setWorkbenchView] = useState<WorkbenchView>("swipe");
  const [profiles, setProfiles] = useState<Record<string, SparcProfile>>({});

  const filtered = useMemo(() => catalog.targets.filter((target) => {
    const text = `${target.name} ${Object.values(target.identifiers).join(" ")}`.toLowerCase();
    if (!text.includes(query.toLowerCase())) return false;
    const rubin = target.layers.find((layer) => layer.id === "rubin-dp2-deep-coadd");
    if (coverageFilter === "rubin") return rubin?.availability !== "not-covered";
    if (coverageFilter === "wise-mass") return target.layers.some((layer) => layer.id === "wise-w1-stellar-mass-2025");
    if (coverageFilter === "no-coverage") return rubin?.availability === "not-covered";
    return true;
  }), [catalog.targets, coverageFilter, query]);

  const loadProfileFor = async (target: LayerTarget) => {
    if (profiles[target.id]) return;
    const profileLayer = target.layers.find((layer) => layer.kind === "profile");
    if (!profileLayer?.assets?.data) return;
    const response = await fetch(profileLayer.assets.data);
    if (!response.ok) return;
    const payload = await response.json() as { target: SparcProfile };
    setProfiles((current) => ({ ...current, [target.id]: payload.target }));
  };

  const chooseTarget = (target: LayerTarget) => {
    const [nextLeft, nextRight] = defaultLayerIds(target);
    setSelectedId(target.id);
    setLeftId(nextLeft);
    setRightId(nextRight);
    const targetComparison = target.comparisons.find((item) => item.status === "published" || item.status === "qa");
    if (targetComparison?.comparisonMode === "catalog-profile") {
      setWorkbenchView("profiles");
      void loadProfileFor(target);
    } else {
      setWorkbenchView(matchingPreview(target.id, targetComparison) ? "swipe" : "evidence");
    }
  };
  const left = layerById(selected, leftId);
  const right = layerById(selected, rightId);
  const comparison = matchingComparison(selected, left.id, right.id);
  const authenticQaViewer = Boolean(matchingPreview(selected.id, comparison) && comparison?.qa?.astrometryPass);
  const candidateViewEnabled = authenticQaViewer && selected.id === "ugc00191";
  const swipeEnabled = Boolean(comparison && comparisonIsSwipeable(comparison, selected.layers)) || Boolean(authenticQaViewer);
  const profilesEnabled = left.kind === "profile" || right.kind === "profile";
  const rankedAudits = useMemo(() => catalog.targets.flatMap((target) =>
    target.comparisons.flatMap((item) => item.assumptionAudits.map((audit) => ({ target, audit })))
  ).sort((a, b) => a.audit.rank - b.audit.rank), [catalog.targets]);

  const loadProfile = async () => {
    await loadProfileFor(selected);
  };

  const selectLayer = (side: "left" | "right", id: string) => {
    if (side === "left") setLeftId(id); else setRightId(id);
    const nextLayer = layerById(selected, id);
    const otherLayer = side === "left" ? right : left;
    const nextView = nextLayer.kind === "profile" || otherLayer.kind === "profile" ? "profiles" : "evidence";
    setWorkbenchView(nextView);
    if (nextView === "profiles") void loadProfile();
  };

  const showProfiles = async () => {
    setWorkbenchView("profiles");
    await loadProfile();
  };

  return (
    <main id="top">
      <header className="layers-header">
        <Link className="layers-brand" href="#top"><span className="brand-glyph"><i /><b /></span><strong>Layers</strong><small>science comparison workspace</small></Link>
        <nav><Link href="/prototype">Real-pixel prototype</Link><a href="#workspace">Workspace</a><a href="#assumptions">Assumptions</a><a href="#method">Method</a><a href="/api/catalog">API</a></nav>
        <span className="release-chip">MULTI-SURVEY · PILOT</span>
      </header>

      <section className="release-bar">
        <div><span className="eyebrow">CURRENT RELEASE</span><h1>See the same sky through every credible layer.</h1><p>Align observations, measure what changed, and find assumptions worth rechecking—without confusing a difference with a discovery.</p></div>
        <div className="release-stats">
          <span><strong>{catalog.summary.targets}</strong><small>targets audited</small></span>
          <span><strong>{catalog.summary.rubinSiaMatches}</strong><small>Rubin footprint matches</small></span>
          <span><strong>{catalog.summary.localImageLayers ?? catalog.summary.rubinUsableLocal}</strong><small>local image layers</small></span>
          <span><strong>{catalog.summary.publishedComparisons}</strong><small>published comparisons</small></span>
        </div>
      </section>

      <section className="layers-workspace" id="workspace">
        <aside className="target-browser">
          <div className="browser-title"><span className="eyebrow">TARGETS</span><strong>{catalog.targetSelection.name}</strong></div>
          <label className="target-search"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Name or identifier" aria-label="Search targets" /></label>
          <div className="filter-tabs" role="group" aria-label="Coverage filter">
            {(["rubin", "wise-mass", "all", "no-coverage"] as CoverageFilter[]).map((item) => <button key={item} className={coverageFilter === item ? "active" : ""} onClick={() => setCoverageFilter(item)}>{item === "rubin" ? "Rubin matches" : item === "wise-mass" ? "WISE masses" : item === "all" ? "All 175" : "No coverage"}</button>)}
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
            <label><span>LAYER A</span><select value={left.id} onChange={(event) => selectLayer("left", event.target.value)}>{selected.layers.map((layer) => <option value={layer.id} key={layer.id}>{layer.survey} · {layer.release}</option>)}</select></label>
            <span className="versus">COMPARE</span>
            <label><span>LAYER B</span><select value={right.id} onChange={(event) => selectLayer("right", event.target.value)}>{selected.layers.map((layer) => <option value={layer.id} key={layer.id}>{layer.survey} · {layer.release}</option>)}</select></label>
          </div>

          <div className="layer-cards"><DataLayerCard layer={left} side="A" /><DataLayerCard layer={right} side="B" /></div>
          <div className="view-tabs">
            <button className={workbenchView === "evidence" ? "active" : ""} onClick={() => setWorkbenchView("evidence")}>Evidence</button>
            <button className={workbenchView === "swipe" ? "active" : ""} disabled={!swipeEnabled} onClick={() => setWorkbenchView("swipe")}>Swipe</button>
            <button className={workbenchView === "coverage" ? "active" : ""} disabled={!authenticQaViewer} onClick={() => setWorkbenchView("coverage")}>Coverage diff</button>
            <button className={workbenchView === "candidates" ? "active" : ""} disabled={!candidateViewEnabled} onClick={() => setWorkbenchView("candidates")}>Signal candidates</button>
            <button className={workbenchView === "profiles" ? "active" : ""} disabled={!profilesEnabled} onClick={showProfiles}>Profiles</button>
            <span>{authenticQaViewer ? `Real ${selected.name} matched pixels · QA only` : "Views activate by data type + QA"}</span>
          </div>
          <LayerViewport key={`${selected.id}-${left.id}-${right.id}`} target={selected} left={left} right={right} view={workbenchView} science={prototypeScience} profile={profiles[selected.id]} />
          <div className="analysis-grid"><DifferencePanel comparison={comparison} /><AssumptionPanel target={selected} comparison={comparison} /></div>
        </section>
      </section>

      <section className="assumption-leaderboard" id="assumptions">
        <div className="leaderboard-heading"><div><span className="eyebrow">RANKED ASSUMPTION AUDITS</span><h2>Assumptions worth rechecking now.</h2></div><p>Image audits rank failed resolved-light transfer gates; catalog audits rank deviations from their declared cross-survey expectation. Both prioritize follow-up and are neither discovery claims nor verdicts on a survey.</p></div>
        <div className="leaderboard-grid">
          {rankedAudits.map(({ target, audit }) => <button key={audit.id} onClick={() => chooseTarget(target)}>
            <span className="leaderboard-rank">#{String(audit.rank).padStart(2, "0")}</span>
            <span><small>{target.name} · {audit.confidence}</small><strong>{audit.title}</strong><em>{audit.evidenceMagnitude.value.toFixed(3)} {audit.evidenceMagnitude.unit} median residual · limit {audit.evidenceMagnitude.passThreshold.toFixed(2)}</em></span>
            <b>{audit.evidenceMagnitude.thresholdMultiple.toFixed(1)}×</b>
          </button>)}
        </div>
      </section>

      <section className="method-band" id="method">
        <div><span className="eyebrow">THE SCIENCE CONTRACT</span><h2>Comparable before compared.</h2><p>Every published result retains original data, provenance, registration, uncertainty, systematic alternatives, and enough information to reproduce the claim.</p></div>
        <ol><li><span>01</span><strong>Locate</strong><p>Find layers covering the declared sky region.</p></li><li><span>02</span><strong>Reconcile</strong><p>Match WCS, footprint, PSF, units, masks, and sky.</p></li><li><span>03</span><strong>Measure</strong><p>Propagate statistical and systematic uncertainty.</p></li><li><span>04</span><strong>Audit</strong><p>Separate observation, inference, and speculation.</p></li></ol>
      </section>

      <footer><Link className="layers-brand" href="#top"><span className="brand-glyph"><i /><b /></span><strong>Layers</strong></Link><p>Independent scientific prototype · No fabricated pixels or claims.</p><div><a href="https://github.com/lrspeiser/layers">Source ↗</a><a href="/api/catalog">Catalog API</a></div></footer>
    </main>
  );
}
