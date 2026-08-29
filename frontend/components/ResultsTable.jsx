function HeroMetrics({ status }) {
  if (!status) return null;
  const aiRate = status.total_settlements > 0
    ? ((status.ai_investigations / status.total_settlements) * 100).toFixed(1)
    : "0.0";
  const matchRate = status.total_settlements > 0
    ? ((status.clean_matches / status.total_settlements) * 100).toFixed(1)
    : "0.0";

  return (
    <div className="card">
      <div className="metrics-grid">
        <div className="metric">
          <div className="value">{status.total_settlements}</div>
          <div className="label">Processed</div>
        </div>
        <div className="metric green">
          <div className="value">{status.clean_matches}</div>
          <div className="label">Clean</div>
        </div>
        <div className="metric orange">
          <div className="value">{status.exceptions + status.math_discrepancies}</div>
          <div className="label">Exceptions</div>
        </div>
        <div className="metric purple">
          <div className="value">{status.unresolved}</div>
          <div className="label">Unresolved</div>
        </div>
        <div className="metric">
          <div className="value" style={{color: 'var(--green)'}}>{status.ai_auto_approved}</div>
          <div className="label">Auto-Approved</div>
        </div>
        <div className="metric blue">
          <div className="value">{status.ai_investigations}</div>
          <div className="label">LLM Investigations ({aiRate}%)</div>
        </div>
      </div>
      <div className="metric-footer">
        Match rate: {matchRate}% &nbsp;&bull;&nbsp; LLM classifies exceptions. Humans decide.
        {status.ai_mode === "demo" && <span style={{marginLeft: 8, color: '#92400e', fontWeight: 600}}>DETERMINISTIC DEMO — heuristic classifications. Set OPENAI_API_KEY for live LLM.</span>}
      </div>
    </div>
  );
}

function ResultsTable({ results, selectedId, onSelect }) {
  const [sortKey, setSortKey] = React.useState("settlement_id");
  const [sortDir, setSortDir] = React.useState("asc");

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
    const arr = [...results];
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
  }, [results, sortKey, sortDir]);

  const SortIcon = ({ col }) => {
    if (sortKey !== col) return <span style={{color:'var(--text-muted)', marginLeft: 4}}>&#8597;</span>;
    return <span style={{marginLeft: 4}}>{sortDir === "asc" ? "&#9650;" : "&#9660;"}</span>;
  };

  return (
    <div className="card">
      <div className="card-header">
        <div>
          <div className="card-title">Reconciliation Results</div>
          <div className="card-subtitle">{results.length} settlements &bull; Click any row to view trace</div>
        </div>
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
            {sorted.map((r, i) => (
              <tr
                key={r.settlement_id || i}
                className={`data-row ${selectedId === r.settlement_id ? "selected" : ""}`}
                onClick={() => onSelect(r)}
              >
                <td className="sid">{r.settlement_id}</td>
                <td><span className={`badge ${badgeClass(r.decision_state)}`}>{r.decision_state.replace(/_/g, ' ')}</span></td>
                <td style={{textAlign:'right', fontWeight: 600, color: r.difference_paise !== 0 ? 'var(--orange)' : 'var(--green)'}}>
                  {formatCurrency(r.difference_paise)}
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
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

window.HeroMetrics = HeroMetrics;
window.ResultsTable = ResultsTable;
