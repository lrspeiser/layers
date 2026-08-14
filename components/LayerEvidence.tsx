import type { Layer } from "@/lib/layers";

export function LayerEvidence({ layer, label }: { layer: Layer; label?: string }) {
  const evidence = layer.linkedEvidence;
  const hasContent = Boolean(layer.assets?.preview || evidence);
  if (!hasContent) return null;

  return (
    <section className="layer-evidence-card">
      {layer.assets?.preview && (
        <div className="layer-evidence-image">
          <img src={layer.assets.preview} alt={`${layer.survey} evidence for this sky field`} />
          <span>{label ?? layer.survey} · authentic public data</span>
        </div>
      )}
      <div className="layer-evidence-body">
        <span className="eyebrow">{evidence?.status ?? `${layer.kind} layer`}</span>
        <h3>{evidence?.headline ?? layer.scienceRole ?? `${layer.survey} ${layer.release}`}</h3>
        <p>{evidence?.summary ?? layer.note}</p>
        {evidence?.facts.length ? (
          <div className="layer-evidence-facts">
            {evidence.facts.map((fact) => (
              <span key={`${fact.label}-${fact.value}`}><small>{fact.label}</small><strong>{fact.value}</strong><em>{fact.unit}</em></span>
            ))}
          </div>
        ) : null}
        {(evidence?.links.length || layer.assets?.data) ? (
          <div className="layer-evidence-links">
            {evidence?.links.map((link) => <a key={link.href} href={link.href} target="_blank" rel="noreferrer">{link.label} ↗</a>)}
            {layer.assets?.data && <a href={layer.assets.data}>Layer data</a>}
          </div>
        ) : null}
      </div>
    </section>
  );
}

export function LayerEvidencePair({ left, right }: { left: Layer; right: Layer }) {
  return (
    <div className="layers-viewport evidence-pair-viewport">
      <div className="evidence-pair-heading">
        <span className="eyebrow">LINKED EVIDENCE · NOT PIXEL SUBTRACTION</span>
        <p>Each layer is shown in its native scientific role. Swipe is enabled only after sky registration and measurement-system QA.</p>
      </div>
      <div className="evidence-pair-grid">
        <LayerEvidence layer={left} label="Layer A" />
        <LayerEvidence layer={right} label="Layer B" />
      </div>
    </div>
  );
}
