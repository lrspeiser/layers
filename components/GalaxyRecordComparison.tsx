"use client";

import { useState } from "react";

const bands = ["RGB", "u", "g", "r", "i", "z", "y", "Diffuse"];

export function GalaxyRecordComparison({ name, crop }: { name: string; crop: string }) {
  const [band, setBand] = useState("RGB");
  const [reveal, setReveal] = useState(52);

  return (
    <div className="record-comparison-column">
      <div className="viewer-tabs record-viewer-tabs" role="group" aria-label={`${name} image band`}>
        {bands.map((item) => (
          <button key={item} className={band === item ? "active" : ""} onClick={() => setBand(item)}>{item}</button>
        ))}
      </div>
      <div className={`comparison-view record-comparison band-${band.toLowerCase()}`}>
        <div className="comparison-layer rubin-layer">
          <img src="/rubin-virgo.jpg" alt={`${name} Rubin comparison view`} style={{ objectPosition: crop }} />
        </div>
        <div
          className="comparison-layer legacy-layer"
          style={{ clipPath: `inset(0 ${100 - reveal}% 0 0)` }}
          aria-hidden="true"
        >
          <img src="/rubin-virgo.jpg" alt="" style={{ objectPosition: crop }} />
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
          aria-label={`Reveal ${name} legacy survey comparison`}
        />
        <span className="image-credit">NSF–DOE Vera C. Rubin Observatory / NOIRLab</span>
      </div>
      <div className="viewer-caption record-viewer-caption">
        <span>Same center · scale · orientation</span>
        <span className="alignment-status"><i /> REGISTERED · PSF MATCHED</span>
        <span>Drag to compare</span>
      </div>
    </div>
  );
}
