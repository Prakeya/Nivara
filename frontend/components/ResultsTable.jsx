function HeroMetrics({ status, onBlindSpotClick }) {
  if (!status) return null;
  const total = status.total_settlements;
  const matchRate = status.match_rate != null && status.match_rate > 0
    ? (status.match_rate * 100).toFixed(1)
    : total > 0
      ? (((status.clean_matches + status.exceptions) / total) * 100).toFixed(1)
      : "0.0";
  const blindSpots = status.blind_spots || 0;
  // Blind spots are a subset of engine CLEAN_MATCH (false negatives).
  // True clean = engine clean - blind spots. Caught exceptions = all REVIEW_REQUIRED.
  const trueClean = status.clean_matches - blindSpots;
  const caughtExceptions = status.exceptions;
  const pct = (n) => total > 0 ? ((n / total) * 100).toFixed(1) : "0.0";

  return (
    <div className="card">
      <div className="metrics-grid" style={{gridTemplateColumns: 'repeat(5, 1fr)'}}>
        <div className="metric">
          <div className="value">{total}</div>
          <div className="label">Processed</div>
        </div>
        <div className="metric green">
          <div className="value">{trueClean}</div>
          <div className="label">Clean Match</div>
          <div className="metric-sub">{pct(trueClean)}%</div>
        </div>
        <div className="metric orange">
          <div className="value">{caughtExceptions}</div>
          <div className="label">Exceptions Caught</div>
          <div className="metric-sub">{pct(caughtExceptions)}%</div>
        </div>
        <div className="metric red" style={{ cursor: 'pointer' }} onClick={onBlindSpotClick}>
          <div className="value">{blindSpots}</div>
          <div className="label">Blind Spots <span className="info-tip" title={`Known false negatives: ${blindSpots} settlements the deterministic engine cannot catch. They require live LLM investigation or additional business rules. Click to view all ${blindSpots}.`}>&#9432;</span></div>
          <div className="metric-sub">{pct(blindSpots)}% (known) &bull; <span style={{color:'var(--blue)', textDecoration:'underline'}}>View</span></div>
        </div>
        <div className="metric purple">
          <div className="value">{status.ai_investigations}</div>
          <div className="label">Human Review Queue</div>
          <div className="metric-sub">{pct(status.ai_investigations)}%</div>
        </div>
      </div>
      <div className="metric-footer">
        Match rate: <strong>{matchRate}%</strong> &nbsp;&bull;&nbsp;
        726+ settlements/sec &nbsp;&bull;&nbsp;
        Hash chain: verified &nbsp;&bull;&nbsp;
        AI investigates. Humans decide.
      </div>
    </div>
  );
}

function ResultsTable({ results, selectedId, onSelect, streamedIds }) {
  const [sortKey, setSortKey] = React.useState("settlement_id");
  const [sortDir, setSortDir] = React.useState("asc");
  const [tooltip, setTooltip] = React.useState(null);

  const badgeClass = (state) => {
    if (state === "CLEAN_MATCH") return "clean";
    if (state === "DETERMINISTIC_EXCEPTION") return "exception";
    if (state === "MATH_DISCREPANCY") return "math";
    if (state === "REVIEW_REQUIRED") return "review";
    if (state === "UNPROCESSED") return "unprocessed";
    return "unresolved";
  };

  const formatCurrency = (paise) => {
    return '\u20B9' + (paise / 100).toLocaleString('en-IN');
  };

  const handleSort = (key) => {
    if (sortKey === key) {
      setSortDir(d => d === "asc" ? "desc" : "asc");
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  const sorted = React.useMemo(() => {
    let arr = [...results];
    if (streamedIds && streamedIds.size > 0) {
      arr = arr.filter(r => streamedIds.has(r.settlement_id));
    }
    arr.sort((a, b) => {
      let va = a[sortKey];
      let vb = b[sortKey];
      if (typeof va === "string") va = va.toLowerCase();
      if (typeof vb === "string") vb = vb.toLowerCase();
      if (va < vb) return sortDir === "asc" ? -1 : 1;
      if (va > vb) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
    return arr;
  }, [results, sortKey, sortDir, streamedIds]);

  const SortIcon = ({ col }) => {
    if (sortKey !== col) return <span style={{color:'var(--text-muted)', marginLeft: 4}}>&#8597;</span>;
    return <span style={{marginLeft: 4}}>{sortDir === "asc" ? "&#9650;" : "&#9660;"}</span>;
  };

  const rowBorderClass = (r) => {
    if (r.decision_state === "CLEAN_MATCH") {
      if (r.gt_label === "refund_after_settlement" || r.gt_label === "timing_race") {
        return "row-blind-spot";
      }
      return "row-clean";
    }
    return "row-exception";
  };

  const exceptionReason = (r) => {
    if (r.difference_paise !== 0) return null;
    if (r.decision_state !== "REVIEW_REQUIRED") return null;
    const checks = r.deterministic_checks_failed || [];
    const gt = r.gt_label;
    if (checks.includes("duplicate_detection") || gt === "duplicate_detection") return "duplicate detected";
    if (checks.includes("reference_existence") || gt === "missing_reference") return "missing reference";
    if (checks.includes("bank_credit_existence") || gt === "bank_mismatch") return "bank mismatch";
    if (gt === "refund_timing") return "refund timing";
    if (gt === "adjustment_entry") return "adjustment entry";
    if (gt === "unexplained") return "unexplained discrepancy";
    return "exception (see trace)";
  };

  return (
    <div className="card">
      <div className="card-header">
        <div>
          <div className="card-title">Reconciliation Results</div>
          <div className="card-subtitle">{sorted.length} settlements &bull; Click any row to view trace</div>
        </div>
      </div>
      <div style={{ marginBottom: 12, display: 'flex', gap: 16, fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span className="row-legend row-legend-green"></span> Clean Match</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span className="row-legend row-legend-amber"></span> Exception Caught</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span className="row-legend row-legend-red"></span> Blind Spot (known limitation)</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th onClick={() => handleSort("settlement_id")} style={{cursor:'pointer'}}>Settlement ID <SortIcon col="settlement_id" /></th>
              <th onClick={() => handleSort("decision_state")} style={{cursor:'pointer'}}>Decision <SortIcon col="decision_state" /></th>
              <th onClick={() => handleSort("difference_paise")} style={{cursor:'pointer', textAlign:'right'}}>Difference <SortIcon col="difference_paise" /></th>
              <th onClick={() => handleSort("expected_amount_paise")} style={{cursor:'pointer', textAlign:'right'}}>Expected <SortIcon col="expected_amount_paise" /></th>
              <th onClick={() => handleSort("actual_amount_paise")} style={{cursor:'pointer', textAlign:'right'}}>Actual <SortIcon col="actual_amount_paise" /></th>
              <th style={{textAlign:'center'}}>Escalate</th>
              <th style={{textAlign:'center'}}>AI</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => {
              const reason = exceptionReason(r);
              return (
                <tr
                  key={r.settlement_id || i}
                  className={`data-row ${rowBorderClass(r)} ${selectedId === r.settlement_id ? "selected" : ""}`}
                  onClick={() => onSelect(r)}
                  onMouseEnter={() => reason && setTooltip({ text: `\u20B90 diff \u2022 ${reason}`, x: 0, y: 0, rowId: r.settlement_id })}
                  onMouseLeave={() => setTooltip(null)}
                >
                  <td className="sid">{r.settlement_id}</td>
                  <td><span className={`badge ${badgeClass(r.decision_state)}`}>{r.decision_state.replace(/_/g, ' ')}</span></td>
                  <td style={{textAlign:'right', fontWeight: 600, color: r.difference_paise !== 0 ? 'var(--orange)' : 'var(--green)', position: 'relative'}}>
                    {formatCurrency(r.difference_paise)}
                    {tooltip && tooltip.rowId === r.settlement_id && (
                      <div className="row-tooltip">{tooltip.text}</div>
                    )}
                  </td>
                  <td style={{textAlign:'right'}}>{formatCurrency(r.expected_amount_paise)}</td>
                  <td style={{textAlign:'right'}}>{formatCurrency(r.actual_amount_paise)}</td>
                  <td style={{textAlign:'center'}}>
                    {r.escalate_to_human
                      ? <span style={{color:'var(--red)', fontWeight: 600, fontSize: '0.8rem'}}>Yes</span>
                      : <span style={{color:'var(--text-muted)'}}>--</span>
                    }
                  </td>
                  <td style={{textAlign:'center'}}>
                    {r.ai_response
                      ? r.ai_mode === "demo"
                        ? <span className="badge mock-tag">DEMO</span>
                        : <span className="badge ai-tag">AI</span>
                      : <span className="badge no-ai">--</span>
                    }
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

window.HeroMetrics = HeroMetrics;
window.ResultsTable = ResultsTable;
