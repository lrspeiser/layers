"use client";

import Link from "next/link";
import { useRef, useState, type PointerEvent as ReactPointerEvent, type WheelEvent } from "react";

type PrototypeQa = {
  residualArcsec: number;
  thresholdArcsec: number;
  commonValidPercent: number;
  matchedSources: number;
};

type CandidateRegion = {
  id: string;
  xPercent: number;
  yPercent: number;
  pixelCount: number;
  peakEmpiricalSigma: number;
  direction: "rubin-excess" | "comparison-excess";
};

export type PrototypeScience = {
  candidateRegions: CandidateRegion[];
  differenceMethod: {
    thresholdEmpiricalSigma: number;
    outerFieldRobustScatterNjy: number;
    stellarZeropointOffsetMag: number;
  };
};

type DragState = { pointerId: number; x: number; y: number; panX: number; panY: number } | null;

const MIN_ZOOM = 1;
const MAX_ZOOM = 8;

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

export function RealFieldPrototype({ qa, science }: { qa: PrototypeQa; science: PrototypeScience }) {
  const [mode, setMode] = useState<"overview" | "compare">("overview");
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [reveal, setReveal] = useState(50);
  const [showCoverage, setShowCoverage] = useState(true);
  const [showMasks, setShowMasks] = useState(true);
  const [showInspector, setShowInspector] = useState(true);
  const [analysisLayer, setAnalysisLayer] = useState<"swipe" | "coverage" | "candidates">("swipe");
  const [selectedCandidate, setSelectedCandidate] = useState<CandidateRegion | null>(null);
  const [pixelsAvailable, setPixelsAvailable] = useState(true);
  const drag = useRef<DragState>(null);

  const setZoomLevel = (next: number) => {
    const value = clamp(next, MIN_ZOOM, MAX_ZOOM);
    setZoom(value);
    if (value === 1) setPan({ x: 0, y: 0 });
  };

  const focusTarget = () => {
    setMode("compare");
    setZoom(4.6);
    setPan({ x: 0, y: 0 });
  };

  const selectAnalysisLayer = (next: "swipe" | "coverage" | "candidates") => {
    setAnalysisLayer(next);
    setSelectedCandidate(null);
  };

  const showFullField = () => {
    setMode("overview");
    setZoom(1);
    setPan({ x: 0, y: 0 });
  };

  const handleWheel = (event: WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    if (mode === "compare") return;
    setZoomLevel(zoom * (event.deltaY < 0 ? 1.18 : 0.84));
  };

  const handlePointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (mode === "compare" || event.button !== 0 || zoom <= 1) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, panX: pan.x, panY: pan.y };
  };

  const handlePointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!drag.current || drag.current.pointerId !== event.pointerId) return;
    setPan({
      x: drag.current.panX + event.clientX - drag.current.x,
      y: drag.current.panY + event.clientY - drag.current.y,
    });
  };

  const endDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (drag.current?.pointerId === event.pointerId) drag.current = null;
  };

  const transformStyle = {
    transform: `translate3d(${pan.x}px, ${pan.y}px, 0) scale(${zoom})`,
    "--inverse-zoom": 1 / zoom,
  } as React.CSSProperties;

  return (
    <main className="prototype-shell">
      <header className="prototype-header">
        <Link className="layers-brand" href="/"><span className="brand-glyph"><i /><b /></span><strong>Layers</strong></Link>
        <div className="prototype-target-title"><span>REAL-PIXEL FIELD 01</span><strong>UGC 00191</strong><small>RA 5.02167° · Dec +10.88000°</small></div>
        <div className="prototype-header-actions"><span className="private-chip">PRIVATE DATA PREVIEW</span><Link href="/">Atlas →</Link></div>
      </header>

      <section className="prototype-facts" aria-label="Field facts">
        <span><small>BASE</small><strong>Rubin DP2</strong><em>authenticated FITS</em></span>
        <span><small>OVERLAP</small><strong>Legacy DR10</strong><em>public FITS</em></span>
        <span><small>FIELD</small><strong>12′ × 12′</strong><em>0.4″ / pixel</em></span>
        <span><small>COMMON PIXELS</small><strong>{qa.commonValidPercent.toFixed(1)}%</strong><em>z-band QA mask</em></span>
        <span><small>REGISTRATION</small><strong>{qa.residualArcsec.toFixed(3)}″</strong><em>P95; limit {qa.thresholdArcsec.toFixed(2)}″</em></span>
      </section>

      <section className="prototype-workspace">
        <div className="real-field-card">
          <div className="field-toolbar">
            <div className="field-mode-tabs" role="group" aria-label="Viewer mode">
              <button className={mode === "overview" ? "active" : ""} onClick={showFullField}>Full Rubin field</button>
              <button className={mode === "compare" ? "active" : ""} onClick={focusTarget}>Aligned comparison</button>
            </div>
            {mode === "compare" && <div className="analysis-layer-tabs" role="group" aria-label="Analysis layer">
              <button className={analysisLayer === "swipe" ? "active" : ""} onClick={() => selectAnalysisLayer("swipe")}>Swipe</button>
              <button className={analysisLayer === "coverage" ? "active" : ""} onClick={() => selectAnalysisLayer("coverage")}>Coverage diff</button>
              <button className={analysisLayer === "candidates" ? "active" : ""} onClick={() => selectAnalysisLayer("candidates")}>Signal candidates</button>
            </div>}
            <div className="field-tools">
              {mode === "overview" ? <>
                <label className="coverage-toggle"><input type="checkbox" checked={showCoverage} onChange={(event) => setShowCoverage(event.target.checked)} /><i /> Legacy footprint</label>
                <button onClick={() => setZoomLevel(zoom / 1.45)} aria-label="Zoom out">−</button>
                <button className="zoom-readout" onClick={showFullField} aria-label="Reset zoom">{zoom.toFixed(1)}×</button>
                <button onClick={() => setZoomLevel(zoom * 1.45)} aria-label="Zoom in">+</button>
              </> : <span className="comparison-lock">VIEW LOCKED · {analysisLayer === "swipe" ? "DRAG REVEAL ONLY" : "ANALYSIS OVERLAY"}</span>}
            </div>
          </div>

          <div
            className={`real-field-viewer mode-${mode} ${zoom > 1 ? "is-zoomed" : ""}`}
            onWheel={handleWheel}
            onPointerDown={handlePointerDown}
            onPointerMove={handlePointerMove}
            onPointerUp={endDrag}
            onPointerCancel={endDrag}
            aria-label="Zoomable Rubin field with Legacy Survey overlap"
          >
            {!pixelsAvailable && (
              <div className="private-pixels-missing">
                <span>AUTHENTICATED PIXELS NOT ON THIS DEPLOYMENT</span>
                <h2>The real-data viewer runs in the private project preview.</h2>
                <p>The interface is public; Rubin DP2 pixels remain local until their redistribution policy is confirmed.</p>
              </div>
            )}
            <div className="field-transform" style={transformStyle}>
              {mode === "compare" && <>
                <img className="field-image" src="/private-preview/ugc00191/legacy-dr10-z.jpg" alt="UGC 00191 in Legacy Survey DR10 z band" onError={() => setPixelsAvailable(false)} draggable={false} />
                {analysisLayer === "swipe" && showMasks && <img className="quality-mask" src="/private-preview/ugc00191/legacy-z-mask.png" alt="Legacy Survey masked or missing pixels" draggable={false} />}
              </>}
              <div className={mode === "compare" ? "rubin-reveal" : "rubin-full"} style={mode === "compare" && analysisLayer === "swipe" ? { clipPath: `inset(0 ${100 - reveal}% 0 0)` } : undefined}>
                <img className="field-image" src={mode === "compare" ? "/private-preview/ugc00191/rubin-dp2-z.jpg" : "/private-preview/ugc00191/rubin-dp2.jpg"} alt={mode === "compare" ? "UGC 00191 in Rubin DP2 z band" : "UGC 00191 in Rubin DP2"} onError={() => setPixelsAvailable(false)} draggable={false} />
                {(mode !== "compare" || analysisLayer === "swipe") && showMasks && <img className="quality-mask" src={mode === "compare" ? "/private-preview/ugc00191/rubin-z-mask.png" : "/private-preview/ugc00191/rubin-mask.png"} alt="Rubin masked or missing pixels" draggable={false} />}
              </div>
              {mode === "compare" && analysisLayer === "coverage" && <img className="science-difference-overlay" src="/private-preview/ugc00191/coverage-difference.png" alt="Red Rubin-only and blue Legacy-only valid-pixel coverage" draggable={false} />}
              {mode === "compare" && analysisLayer === "candidates" && <>
                <img className="science-difference-overlay" src="/private-preview/ugc00191/candidate-difference.png" alt="Red Rubin-excess and blue Legacy-excess empirical residual candidates" draggable={false} />
                {science.candidateRegions.map((candidate, index) => <button className={`difference-pin direction-${candidate.direction}`} key={candidate.id} style={{ left: `${candidate.xPercent}%`, top: `${candidate.yPercent}%` }} onClick={() => setSelectedCandidate(candidate)} aria-label={`Inspect candidate ${index + 1}`}>{index + 1}</button>)}
              </>}
              {showCoverage && mode === "overview" && <img className="coverage-footprint" src="/private-preview/ugc00191/legacy-coverage.png" alt="Legacy Survey valid-pixel footprint" draggable={false} />}
              {mode === "overview" && <span className="science-region" aria-hidden="true"><i>SPARC 1.27′ region</i></span>}
              <button className="overlap-pin" onClick={focusTarget} aria-label="Zoom to UGC 00191 overlap">
                <span className="pin-pulse" /><span className="pin-core" /><strong>UGC 00191</strong><small>Rubin + Legacy + SPARC</small>
              </button>
            </div>

            {mode === "overview" ? (
              <div className="overview-caption"><span><i /> Legacy valid-pixel footprint · <b /> amber hatch = masked Rubin pixels</span><strong>Click the pin to inspect the overlap</strong></div>
            ) : (
              <>
                {analysisLayer === "swipe" && <><span className="image-label image-label-left">RUBIN DP2 · z</span>
                <span className="image-label image-label-right">LEGACY DR10 · z</span>
                <span className="compare-rule" style={{ left: `${reveal}%` }}><i>↔</i></span>
                <input className="compare-slider" type="range" min="2" max="98" value={reveal} onChange={(event) => setReveal(Number(event.target.value))} aria-label="Reveal Rubin DP2 over Legacy DR10" />
                <label className="mask-toggle"><input type="checkbox" checked={showMasks} onChange={(event) => setShowMasks(event.target.checked)} /> show data-quality masks</label></>}
                {analysisLayer === "coverage" && <div className="difference-legend"><strong>COVERAGE</strong><span className="legend-red">Rubin valid / Legacy missing</span><span className="legend-blue">Legacy valid / Rubin missing</span><small>Exact mask comparison · not a brightness claim</small></div>}
                {analysisLayer === "candidates" && <div className="difference-legend"><strong>EMPIRICAL SIGNAL CANDIDATES</strong><span className="legend-red">Rubin excess</span><span className="legend-blue">Legacy excess</span><small>≥ {science.differenceMethod.thresholdEmpiricalSigma.toFixed(0)}σ-like outer-field residual · click a numbered region</small></div>}
                {selectedCandidate && <div className="candidate-popover"><button onClick={() => setSelectedCandidate(null)} aria-label="Close candidate">×</button><strong>{selectedCandidate.direction === "rubin-excess" ? "RUBIN-EXCESS CANDIDATE" : "LEGACY-EXCESS CANDIDATE"}</strong><span>{Math.abs(selectedCandidate.peakEmpiricalSigma) > 99 ? ">99" : Math.abs(selectedCandidate.peakEmpiricalSigma).toFixed(1)}σ-like peak · {selectedCandidate.pixelCount} connected pixels</span><p>This is a place to inspect, not a discovery. Variable sources, residual PSF shape, filter response, masking, or diffuse light can all produce it.</p></div>}
                <button className="data-inspector-toggle" onClick={() => setShowInspector((value) => !value)}>{showInspector ? "HIDE" : "SHOW"} DATA INSPECTOR</button>
                <span className="visual-qa-chip">LOCKED VIEW · FLUX-CONSERVING GRID · EXTENDED-SOURCE QA PENDING</span>
              </>
            )}
          </div>
          <div className="field-bottomline">
            <span>{mode === "compare" ? analysisLayer === "swipe" ? "Drag horizontally to reveal · field position locked" : "Analysis overlay · field position locked" : "Scroll or use + / − to zoom · drag while zoomed"}</span>
            <span>North up · east left</span>
            <span>Display stretch; calibrated FITS drive analysis</span>
          </div>
        </div>

        <aside className="prototype-analysis">
          {showInspector && <section className="prototype-panel data-inspector-panel">
            <div className="panel-heading"><span className="eyebrow">WHAT IS BEHIND EACH PIXEL?</span><span>CALIBRATED FITS</span></div>
            <p className="mono-explainer"><strong>Why grayscale?</strong> The aligned view compares one physical band—z—to keep artificial color recipes from looking like new structure. The full-field view remains a color composite for orientation. The picture is only a stretch; every location retains calibrated data.</p>
            <div className="pixel-payload">
              <span><i>01</i><strong>FLUX</strong><small>nJy per 0.4″ pixel</small></span>
              <span><i>02</i><strong>UNCERTAINTY</strong><small>variance / inverse variance</small></span>
              <span><i>03</i><strong>QUALITY</strong><small>mask bits + valid coverage</small></span>
              <span><i>04</i><strong>POSITION</strong><small>ICRS coordinate from WCS</small></span>
            </div>
          </section>}

          {showInspector && <section className="prototype-panel better-panel">
            <div className="panel-heading"><span className="eyebrow">WHICH IS BETTER?</span><span>DEPENDS ON THE QUESTION</span></div>
            <h3>No single winner. Compare the dimensions.</h3>
            <div className="better-table" role="table" aria-label="Rubin and Legacy data quality comparison">
              <div className="better-head" role="row"><span>DIMENSION</span><strong>RUBIN DP2 z</strong><strong>LEGACY DR10 z</strong></div>
              <div role="row"><span>Valid field</span><strong>96.2%</strong><strong className="dimension-winner">99.6% ↑</strong></div>
              <div role="row"><span>Bands here</span><strong className="dimension-winner">g · r · i · z ↑</strong><strong>g · r · z</strong></div>
              <div role="row"><span>Median formal pixel noise*</span><strong>139.0 nJy</strong><strong className="dimension-winner">88.8 nJy ↑</strong></div>
              <div role="row"><span>Measured FWHM</span><strong>2.06″</strong><strong className="dimension-winner">2.03″ ↑</strong></div>
              <div role="row"><span>Native support</span><strong>image · variance · mask</strong><strong>image · inverse variance · mask</strong></div>
            </div>
            <p className="scorecard-note">*After flux-conserving resampling to 0.4″ pixels. Lower is better, but interpolation covariance means this is not yet a point-source or surface-brightness depth measurement. The measured resolution is effectively tied.</p>
            <div className="provisional-verdict"><strong>PROVISIONAL READ</strong><p>Rubin carries an extra usable band; Legacy is more spatially complete and has lower formal z-band pixel noise here. Neither wins on faint outer light until correlated noise, PSF wings, extended-source color transfer, and injection/recovery are measured.</p></div>
          </section>}

          <section className="prototype-panel overlap-panel">
            <div className="panel-heading"><span className="eyebrow">OVERLAP FOUND</span><span className="qa-pass-dot">ASTROMETRY PASS</span></div>
            <h1>One sky position.<br />Three useful layers.</h1>
            <div className="layer-stack-mini">
              <span><i className="rubin-color" /><strong>Rubin DP2</strong><small>four-band optical image</small></span>
              <span><i className="legacy-color" /><strong>Legacy DR10</strong><small>reference image</small></span>
              <span><i className="sparc-color" /><strong>SPARC</strong><small>rotation + mass profile</small></span>
            </div>
          </section>

          <section className="prototype-panel evidence-panel">
            <span className="eyebrow">WHAT WE KNOW NOW</span>
            <div className="evidence-number"><strong>{qa.residualArcsec.toFixed(3)}″</strong><span>P95 registration residual<small>{qa.matchedSources} matched sources</small></span></div>
            <div className="threshold-line"><i style={{ width: `${Math.min(100, qa.residualArcsec / qa.thresholdArcsec * 100)}%` }} /><b /><span>0″</span><span>pass limit {qa.thresholdArcsec.toFixed(2)}″</span></div>
            <p>The layers line up closely enough for visual review. This does <strong>not</strong> yet mean brightness differences are scientifically comparable.</p>
            <div className="valid-pixel-pair"><span><small>RUBIN VALID z PIXELS</small><strong>96.2%</strong></span><span><small>LEGACY VALID z PIXELS</small><strong>99.6%</strong></span></div>
            <p>Legacy really does fill more of this field: 3.4 percentage points more valid z-band pixels. That is coverage and masking—not evidence that it detects more faint light.</p>
          </section>

          <section className="prototype-panel next-panel">
            <span className="eyebrow">NEXT SCIENCE GATE</span>
            <h3>Turn a good-looking slider into a defensible comparison.</h3>
            <ol>
              <li className="done"><i>✓</i><span><strong>Common sky grid</strong><small>same 12′ WCS and pixels</small></span></li>
              <li className="done"><i>✓</i><span><strong>Astrometry</strong><small>below declared threshold</small></span></li>
              <li className="done"><i>✓</i><span><strong>PSF + sky intermediate</strong><small>matched FITS pair; ~1.3% FWHM difference</small></span></li>
              <li className="done"><i>✓</i><span><strong>Held-out stellar color term</strong><small>122 stars · 0.040 mag validation RMS</small></span></li>
              <li><i>5</i><span><strong>Extended-source + recovery tests</strong><small>required before a missing-light claim</small></span></li>
            </ol>
          </section>

          <section className="prototype-panel source-panel">
            <div className="panel-heading"><span className="eyebrow">SOURCE + PROVENANCE</span><span>FILES RETAINED</span></div>
            <h3>Legacy Survey DR10 public coadds</h3>
            <p>Downloaded from the official Legacy Survey FITS cutout service at RA 5.02167°, Dec +10.88000°. Sixteen original 512-pixel FITS tiles retain the griz science cube, inverse variance, source URL, and SHA-256.</p>
            <dl>
              <div><dt>Original FITS</dt><dd>16 · 128.1 MiB</dd></div>
              <div><dt>Local products</dt><dd>g · r · z + IVAR + mask</dd></div>
              <div><dt>Pipeline layer</dt><dd>ls-dr10</dd></div>
            </dl>
            <a href="https://www.legacysurvey.org/viewer/fits-cutout?ra=5.02167&dec=10.88000&size=512&layer=ls-dr10&pixscale=0.4&bands=griz&invvar=" target="_blank" rel="noreferrer">Open an official source FITS ↗</a>
          </section>

          <section className="prototype-panel prototype-question">
            <span className="eyebrow">FEEDBACK TARGET</span>
            <p>Does the full-field pin make the tiny scientific overlap discoverable? Is the transition into the comparison the right level of zoom?</p>
          </section>
        </aside>
      </section>
    </main>
  );
}
