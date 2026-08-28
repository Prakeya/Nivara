function BatchPatterns({ patterns }) {
  if (!patterns || patterns.length === 0) return (
    <div className="card">
      <div className="empty-state">
        <div className="empty-icon">🔍</div>
        <div className="empty-title">No batch patterns detected</div>
        <div className="empty-hint">The batch analyzer did not find cross-settlement patterns in this data.</div>
      </div>
    </div>
  );

  const patternLabel = (type) => {
    if (type === "SYSTEMATIC_FEE_ROUNDING") return "Systematic Fee Rounding";
    if (type === "REPEATED_BANK_DELAY") return "Repeated Bank Delay";
    if (type === "REFUND_CLUSTER") return "Refund Cluster";
    if (type === "REPEATED_UNEXPLAINED_GAP") return "Repeated Unexplained Gap";
    return type.replace(/_/g, ' ');
  };

  const confidenceColor = (c) => {
    if (c >= 0.8) return 'var(--green)';
    if (c >= 0.5) return 'var(--orange)';
    return 'var(--text-muted)';
  };

  return (
    <div className="card">
      <div className="card-header">
        <div>
          <div className="card-title">Batch Patterns</div>
          <div className="card-subtitle">{patterns.length} cross-settlement pattern{patterns.length !== 1 ? 's' : ''} detected</div>
        </div>
      </div>

      {patterns.map((p, i) => (
        <div key={i} className="pattern-card">
          <div className="flex-between">
            <div className="pattern-type">{patternLabel(p.pattern_type)}</div>
            <div style={{display:'flex', alignItems:'center', gap: 8}}>
              <div style={{width:60, height:6, background:'var(--border)', borderRadius:3, overflow:'hidden'}}>
                <div style={{width: `${p.confidence * 100}%`, height:'100%', background: confidenceColor(p.confidence), borderRadius:3}} />
              </div>
              <span style={{fontSize:'0.75rem', fontWeight:600, color: confidenceColor(p.confidence)}}>{(p.confidence * 100).toFixed(0)}%</span>
            </div>
          </div>
          <div className="pattern-desc">{p.description}</div>
          <div className="pattern-meta">
            Affected: {p.affected_settlement_ids.slice(0, 6).join(', ')}
            {p.affected_settlement_ids.length > 6 ? ` +${p.affected_settlement_ids.length - 6} more` : ''}
            {p.recommended_action && <span> &bull; {p.recommended_action}</span>}
          </div>
        </div>
      ))}
    </div>
  );
}

window.BatchPatterns = BatchPatterns;
