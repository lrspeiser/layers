"use client";

export type SignalCandidate = {
  id: string;
  xPercent: number;
  yPercent: number;
  pixelCount: number;
  peakEmpiricalSigma: number;
  direction: "rubin-excess" | "comparison-excess";
};

function candidateExplanation(candidate: SignalCandidate) {
  if (candidate.direction === "rubin-excess") {
    return {
      label: "Rubin brighter",
      meaning: "Rubin is brighter than the provisional Legacy prediction at this location. That could be real structure, a variable source, filter mismatch, a PSF residual, or masking.",
    };
  }
  return {
    label: "Legacy brighter",
    meaning: "Legacy is brighter than the provisional Rubin prediction at this location. That could be real structure, a variable source, filter mismatch, a PSF residual, or missing Rubin pixels.",
  };
}

export function SignalCandidateList({
  candidates,
  onSelect,
}: {
  candidates: SignalCandidate[];
  onSelect?: (candidate: SignalCandidate) => void;
}) {
  return (
    <section className="candidate-breakout" aria-labelledby="candidate-breakout-title">
      <div className="candidate-breakout-heading">
        <div><span className="eyebrow">SIGNAL CANDIDATES</span><h3 id="candidate-breakout-title">Places to investigate next</h3></div>
        <p>These are residual-screening leads, not detections. The peak is measured against this field&apos;s empirical residual scatter; it is not calibrated astrophysical significance.</p>
      </div>
      <div className="candidate-card-grid">
        {candidates.map((candidate, index) => {
          const explanation = candidateExplanation(candidate);
          const peak = Math.abs(candidate.peakEmpiricalSigma);
          const content = <>
            <div className="candidate-card-top"><span className={`candidate-direction direction-${candidate.direction}`}>{explanation.label}</span><strong>#{String(index + 1).padStart(2, "0")}</strong></div>
            <h4>Residual region {index + 1}</h4>
            <div className="candidate-card-stats"><span><small>EMPIRICAL PEAK</small><b>{peak > 99 ? ">99" : peak.toFixed(1)}x</b></span><span><small>CONNECTED AREA</small><b>{candidate.pixelCount} px</b></span></div>
            <p><strong>What it might mean</strong>{explanation.meaning}</p>
            <p><strong>Explore next</strong>Inspect masks and source shape; repeat in adjacent bands and external surveys; then rerun after the extended-source color transfer passes.</p>
            <small className="candidate-location">Viewer position: {candidate.xPercent.toFixed(1)}% x / {candidate.yPercent.toFixed(1)}% y</small>
          </>;
          return onSelect ? <button className="candidate-card" key={candidate.id} onClick={() => onSelect(candidate)}>{content}<em>Show in viewer &uarr;</em></button> : <article className="candidate-card" key={candidate.id}>{content}</article>;
        })}
      </div>
    </section>
  );
}
