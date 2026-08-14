"use client";

import { useState } from "react";
import styles from "./TractLayerComposite.module.css";

export type CompositeLayer = {
  id: string;
  surveyName: string;
  family: string;
  band: string;
  image: string;
  tint: string;
  placement: {
    leftPercent: number;
    topPercent: number;
    widthPercent: number;
    heightPercent: number;
    requiresRepositioning: boolean;
    note: string;
  };
  geometry: { widthArcmin: number; heightArcmin: number; centreOffsetArcmin: number };
};

export function TractLayerComposite({
  tract,
  baseImage,
  baseLabel,
  baseFrameArcmin,
  layers,
}: {
  tract: number;
  baseImage: string;
  baseLabel: string;
  baseFrameArcmin: number;
  layers: CompositeLayer[];
}) {
  const [active, setActive] = useState<Record<string, number>>({});

  function setOpacity(id: string, value: number) {
    setActive((current) => ({ ...current, [id]: value }));
  }

  return (
    <div className={styles.composite}>
      <figure className={styles.stage}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img className={styles.base} src={baseImage} alt={`${baseLabel} in tract ${tract}`} draggable={false} />
        {layers.map((layer) => {
          const opacity = active[layer.id] ?? 0;
          if (opacity <= 0) return null;
          return (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              key={layer.id}
              src={layer.image}
              alt={`${layer.surveyName} ${layer.band} positioned over tract ${tract}`}
              draggable={false}
              style={{
                left: `${layer.placement.leftPercent}%`,
                top: `${layer.placement.topPercent}%`,
                width: `${layer.placement.widthPercent}%`,
                height: `${layer.placement.heightPercent}%`,
                opacity: opacity / 100,
                mixBlendMode: "screen",
                // The tint separates layers that would otherwise be
                // indistinguishable greyscale, without altering the pixels'
                // relative brightness within a layer.
                filter: `sepia(1) saturate(6) hue-rotate(${layer.tint}deg)`,
              }}
            />
          );
        })}
        <figcaption>{baseLabel} · {baseFrameArcmin.toFixed(2)}′ field</figcaption>
        <span className={styles.compass}>NORTH UP · EAST LEFT</span>
      </figure>

      <div className={styles.panel}>
        <header>
          <span>OVERLAY OTHER SURVEYS</span>
          <p>
            Each layer is placed by its own WCS, not stretched to fill the frame, so a survey covering a
            different field sits where it actually is on the sky.
          </p>
        </header>

        {layers.length === 0 ? (
          <p className={styles.baseNote}>No other measured layer covers this tract yet.</p>
        ) : (
          layers.map((layer) => {
            const opacity = active[layer.id] ?? 0;
            return (
              <div className={styles.layer} key={layer.id}>
                <label className={styles.layerTop}>
                  <input
                    type="checkbox"
                    checked={opacity > 0}
                    onChange={(event) => setOpacity(layer.id, event.target.checked ? 65 : 0)}
                  />
                  <strong>{layer.surveyName}</strong>
                  <i style={{ background: `hsl(${(Number(layer.tint) + 40) % 360} 80% 60%)` }} aria-hidden="true" />
                </label>
                <small>
                  {layer.band} · {layer.geometry.widthArcmin.toFixed(2)}′ × {layer.geometry.heightArcmin.toFixed(2)}′
                  {layer.placement.requiresRepositioning && (
                    <>
                      {" "}
                      <span className={styles.repositioned}>
                        repositioned {layer.geometry.centreOffsetArcmin.toFixed(2)}′
                      </span>
                    </>
                  )}
                </small>
                {opacity > 0 && (
                  <input
                    type="range"
                    min={5}
                    max={100}
                    value={opacity}
                    onChange={(event) => setOpacity(layer.id, Number(event.target.value))}
                    aria-label={`${layer.surveyName} opacity`}
                  />
                )}
              </div>
            );
          })
        )}

        <p className={styles.baseNote}>
          Co-display only. These layers are positioned to the same sky, not PSF-matched, bandpass-matched,
          or flux-matched, so a brightness difference between them is not a measurement.
        </p>
      </div>
    </div>
  );
}

export default TractLayerComposite;
