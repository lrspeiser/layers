"use client";

import { useState } from "react";
import type { Comparison, LayerTarget } from "@/lib/layers";

type Preview = {
  band: string;
  commonValidPixelFraction: number;
  coverageFractions: { rubinOnly: number; referenceOnly: number; neither: number };
  assets: { rubin: { path: string }; reference: { path: string }; commonCoverage: { path: string } };
  notice: string;
};

export default function TargetComparisonViewer({ target, comparison, preview }: { target: LayerTarget; comparison: Comparison; preview: Preview }) {
  const [reveal, setReveal] = useState(50);
  const [view, setView] = useState<"swipe" | "coverage">("swipe");
  const [pixelsAvailable, setPixelsAvailable] = useState(true);
  const reference = target.layers.find((layer) => layer.id === comparison.layerIds[1]);
  const qa = comparison.qa;

  return <section className="record-comparison-workbench">
    <div className="record-comparison-toolbar">
      <div><span className="eyebrow">FUNCTIONAL MATCHED-PIXEL EXAMPLE</span><h2>Rubin DP2 <i>vs</i> {reference?.survey ?? qa?.comparisonLayerLabel}</h2></div>
      <div><button className={view === "swipe" ? "active" : ""} onClick={() => setView("swipe")}>Swipe</button><button className={view === "coverage" ? "active" : ""} onClick={() => setView("coverage")}>Coverage mask</button></div>
    </div>
    <div className="record-comparison-viewer">
      {pixelsAvailable ? <>
        <img src={preview.assets.reference.path} alt={`${target.name}, ${reference?.survey ?? "reference survey"}, matched ${preview.band} band`} onError={() => setPixelsAvailable(false)} draggable={false} />
        <div className="record-reveal" style={{ clipPath: `inset(0 ${100 - reveal}% 0 0)` }}><img src={preview.assets.rubin.path} alt={`${target.name}, Rubin DP2, matched ${preview.band} band`} onError={() => setPixelsAvailable(false)} draggable={false} /></div>
        {view === "coverage" && <img className="record-coverage-overlay" src={preview.assets.commonCoverage.path} alt="Red marks Rubin-only coverage, blue reference-only coverage, and amber neither usable" />}
        {view === "swipe" && <><span className="viewport-label label-left">Rubin DP2 · {preview.band}</span><span className="viewport-label label-right">{reference?.survey} · {preview.band}</span><span className="reveal-rule" style={{ left: `${reveal}%` }}><i>↔</i></span><input type="range" min="2" max="98" value={reveal} onChange={(event) => setReveal(Number(event.target.value))} aria-label={`Reveal Rubin DP2 over ${reference?.survey}`} /></>}
        {view === "coverage" && <div className="main-overlay-legend"><strong>PER-LAYER COVERAGE</strong><span className="record-mask-key">Red Rubin only · blue {reference?.survey} only · amber neither</span><small>{(preview.commonValidPixelFraction * 100).toFixed(1)}% shared analysis area</small></div>}
      </> : <div className="qa-pixel-fallback"><span className="eyebrow">AUTHENTIC PIXELS REQUIRE DATA ACCESS</span><h3>The comparison metadata and checksums are public; this host cannot redistribute its protected Rubin preview.</h3><p>Use the private deployment or local repository with the authenticated DP2 layer store.</p></div>}
    </div>
    <div className="record-comparison-facts">
      <span><small>ASTROMETRY</small><strong>{qa?.postMatchAstrometricResidualP95Arcsec?.toFixed(3) ?? "—"}″ p95</strong><em>limit {comparison.registration?.qaThresholdArcsec?.toFixed(2) ?? "—"}″</em></span>
      <span><small>POINT-SOURCE FILTER</small><strong>{qa?.pointSourceCalibrationPass ? "PASS" : "BLOCKED"}</strong><em>{qa?.filterHeldOutRmsMag?.toFixed(3) ?? "—"} mag held-out RMS</em></span>
      <span><small>RESOLVED TRANSFER</small><strong>{qa?.extendedSourceTransferPass ? "PASS" : "BLOCKED"}</strong><em>{qa?.extendedSourceResolvedCells ?? "—"} cells · {qa?.extendedSourceMedianAbsoluteResidualMag?.toFixed(3) ?? "—"} mag residual</em></span>
      <span><small>SCIENCE CLAIM</small><strong>NOT PUBLISHED</strong><em>observation ≠ inference</em></span>
    </div>
    <p className="record-preview-notice">{preview.notice}</p>
  </section>;
}
