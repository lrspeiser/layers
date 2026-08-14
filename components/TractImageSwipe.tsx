"use client";

import { useState } from "react";
import Image from "next/image";
import styles from "./TractImageSwipe.module.css";

export type TractImageSwipeProps = {
  tract: number;
  rubinImage: string;
  referenceImage: string;
  coverageImage: string;
  overlayImage: string;
  rubinLabel: string;
  referenceLabel: string;
};

export function TractImageSwipe(props: TractImageSwipeProps) {
  const [mode, setMode] = useState<"swipe" | "coverage" | "overlay">("swipe");
  const [reveal, setReveal] = useState(50);
  return (
    <div className={styles.workspace}>
      <div className={styles.toolbar} role="group" aria-label="Tract comparison layer">
        <button type="button" aria-pressed={mode === "swipe"} onClick={() => setMode("swipe")}>Swipe</button>
        <button type="button" aria-pressed={mode === "coverage"} onClick={() => setMode("coverage")}>Valid coverage</button>
        <button type="button" aria-pressed={mode === "overlay"} onClick={() => setMode("overlay")}>Position overlay</button>
      </div>
      <div className={styles.viewer}>
        {mode === "swipe" ? <>
          <Image src={props.referenceImage} alt={`${props.referenceLabel} pixels in tract ${props.tract}`} fill sizes="(max-width: 900px) 100vw, 760px" draggable={false} />
          <div className={styles.reveal} style={{ clipPath: `inset(0 ${100 - reveal}% 0 0)` }}><Image src={props.rubinImage} alt={`${props.rubinLabel} pixels in tract ${props.tract}`} fill sizes="(max-width: 900px) 100vw, 760px" draggable={false} /></div>
          <span className={styles.rule} style={{ left: `${reveal}%` }} aria-hidden="true" />
          <input type="range" min="2" max="98" value={reveal} onChange={(event) => setReveal(Number(event.target.value))} aria-label={`Reveal ${props.rubinLabel} over ${props.referenceLabel}`} />
          <strong className={styles.leftLabel}>{props.rubinLabel}</strong><strong className={styles.rightLabel}>{props.referenceLabel}</strong>
        </> : <Image src={mode === "coverage" ? props.coverageImage : props.overlayImage} alt={mode === "coverage" ? `Red, blue, and white valid-pixel coverage in tract ${props.tract}` : `Orange and cyan positional overlay in tract ${props.tract}`} fill sizes="(max-width: 900px) 100vw, 760px" draggable={false} />}
      </div>
      <p>{mode === "swipe" ? "Drag the handle to switch between two independently stretched views. The sky position stays locked. Pixels outside the common valid footprint are black in both views, so shared black shapes are coverage masks—not objects in the sky." : mode === "coverage" ? "White: common valid support · red: Rubin only · blue: reference only. This is coverage, not brightness difference." : "Orange/cyan co-display shows positional structure only; it is not a flux subtraction."}</p>
    </div>
  );
}

export default TractImageSwipe;
