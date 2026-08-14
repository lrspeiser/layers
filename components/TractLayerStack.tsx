"use client";

import { useState } from "react";
import { TractImageSwipe } from "./TractImageSwipe";
import styles from "./TractLayerStack.module.css";

export type TractLayerProduct = {
  id: string;
  family: string;
  surveyName: string;
  release: string;
  rubinBand: string;
  referenceBand: string;
  referenceUnit: string;
  commonCoverageFraction: number;
  rubinImage: string;
  referenceImage: string;
  coverageImage: string;
  overlayImage: string;
  interpretation: string;
  blockers: string[];
};

const FAMILY_LABELS: Record<string, string> = {
  optical: "Optical",
  "uv-ir": "UV / infrared",
  radio: "Radio",
  "x-ray": "X-ray",
  "high-energy": "X-ray",
  gas: "Neutral gas",
  "neutral-gas": "Neutral gas",
  "time-domain": "Time domain",
  lensing: "Lensing",
  "cmb-large-scale-structure": "Lensing / CMB",
};

export function TractLayerStack({ tract, products }: { tract: number; products: TractLayerProduct[] }) {
  const [activeId, setActiveId] = useState(products[0]?.id ?? "");
  const active = products.find((product) => product.id === activeId) ?? products[0];
  if (!active) return null;

  return (
    <div className={styles.stack}>
      <div className={styles.tabs} role="tablist" aria-label={`Available aligned layers for Rubin tract ${tract}`}>
        {products.map((product) => (
          <button
            key={product.id}
            type="button"
            role="tab"
            aria-selected={product.id === active.id}
            onClick={() => setActiveId(product.id)}
          >
            <span>{FAMILY_LABELS[product.family] ?? product.family}</span>
            <strong>{product.surveyName}</strong>
            <small>{product.referenceBand}</small>
          </button>
        ))}
      </div>

      <div className={styles.summary}>
        <div><span>ACTIVE REFERENCE</span><strong>{active.surveyName}</strong><small>{active.release}</small></div>
        <div><span>OBSERVABLE</span><strong>{active.referenceBand}</strong><small>{active.referenceUnit}</small></div>
        <div><span>COMMON SUPPORT</span><strong>{(active.commonCoverageFraction * 100).toFixed(2)}%</strong><small>finite pixels on the display grid</small></div>
      </div>

      <TractImageSwipe
        key={active.id}
        tract={tract}
        rubinImage={active.rubinImage}
        referenceImage={active.referenceImage}
        coverageImage={active.coverageImage}
        overlayImage={active.overlayImage}
        rubinLabel={`Rubin DP2 · ${active.rubinBand}`}
        referenceLabel={`${active.surveyName} · ${active.referenceBand}`}
      />

      <div className={styles.guardrail}>
        <p><strong>What this view supports:</strong> {active.interpretation}</p>
        <p><strong>Before a quantitative difference:</strong> {active.blockers.join(" · ")}</p>
      </div>
    </div>
  );
}

export default TractLayerStack;
