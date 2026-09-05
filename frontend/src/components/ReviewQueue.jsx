import React from 'react';
function ReviewQueue({ results, onSelect, onReview }) {
  const queue = results.filter(r => r.escalate_to_human && (!r.resolution_status || r.resolution_status === "OPEN"));

  const badgeClass = (state) => {
    if (state === "MATH_DISCREPANCY") return "math";
    if (state === "REVIEW_REQUIRED") return "review";
    if (state === "DETERMINISTIC_EXCEPTION") return "exception";
    if (state === "UNPROCESSED") return "unprocessed";
    return "unresolved";
  };

  const reason = (r) => {
    if (r.decision_state === "MATH_DISCREPANCY") return "Difference between expected and actual — no deterministic cause found";
    if (r.decision_state === "REVIEW_REQUIRED") return "Exception analysis completed — requires human decision";
    if (r.decision_state === "DETERMINISTIC_EXCEPTION") return r.deterministic_checks_failed.join(", ").replace(/_/g, " ");
    if (r.decision_state === "UNPROCESSED") return "Engine error — settlement could not be processed";
    return "Requires review";
  };

  const formatCurrency = (paise) => '\u20B9' + (paise / 100).toLocaleString('en-IN');

  if (queue.length === 0) return (
    <div className="card">
      <div className="empty-state">
        <div className="empty-icon">✅</div>
        <div className="empty-title">Review queue is empty</div>
        <div className="empty-hint">No settlements require human attention.</div>
      </div>
    </div>
  );

  return (
    <div className="card">
      <div className="card-header">
        <div>
          <div className="card-title">Review Queue</div>
          <div className="card-subtitle">{queue.length} settlements requiring human attention</div>
        </div>
      </div>

      <div style={{ padding: '10px 14px', background: 'var(--orange-bg)', borderRadius: 'var(--radius)', border: '1px solid var(--orange)', fontSize: '0.85rem', color: 'var(--orange)', marginBottom: 16 }}>
        These settlements have been escalated for human review. Nivara's AI investigates every discrepancy and generates structured evidence, but never auto-approves financial transactions. Humans retain final authority.
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Settlement ID</th>
              <th>Decision</th>
              <th style={{textAlign:'right'}}>Difference</th>
              <th>Reason</th>
              <th>Analysis</th>
              <th style={{textAlign:'center'}}>Action</th>
            </tr>
          </thead>
          <tbody>
            {queue.map((r, i) => (
              <tr key={r.settlement_id || i} className="data-row" onClick={() => onSelect(r)}>
                <td className="sid">{r.settlement_id}</td>
                <td><span className={`badge ${badgeClass(r.decision_state)}`}>{r.decision_state.replace(/_/g, ' ')}</span></td>
                <td style={{textAlign:'right', fontWeight: 600, color: 'var(--orange)'}}>{formatCurrency(r.difference_paise)}</td>
                <td style={{fontSize: '0.8rem', color: 'var(--text-secondary)', maxWidth: 280}}>{reason(r)}</td>
                <td>
                  {r.ai_response
                    ? <span style={{fontSize:'0.8rem'}}>
                        {r.ai_mode === "demo" && <span className="badge mock-tag" style={{marginRight: 4}}>DEMO</span>}
                        {r.ai_response.classification} {r.ai_mode === "demo" ? "(heuristic)" : `(${(r.ai_response.raw_confidence * 100).toFixed(0)}%)`}
                      </span>
                    : <span style={{fontSize:'0.8rem', color:'var(--text-muted)'}}>Pending</span>
                  }
                </td>
                <td style={{textAlign:'center'}}>
                  {onReview ? (
                    <div style={{display:'flex', gap: 4, justifyContent:'center'}}>
                      <button className="btn btn-sm" style={{background:'var(--green)', color:'#fff8ee', fontSize:'0.75rem', padding:'4px 8px'}}
                        onClick={(e) => { e.stopPropagation(); onReview(r.settlement_id, 'APPROVE'); }}>Approve</button>
                      <button className="btn btn-sm" style={{background:'var(--red)', color:'#fff8ee', fontSize:'0.75rem', padding:'4px 8px'}}
                        onClick={(e) => { e.stopPropagation(); onReview(r.settlement_id, 'REJECT'); }}>Reject</button>
                    </div>
                  ) : (
                    <button className="btn btn-sm" onClick={(e) => { e.stopPropagation(); onSelect(r); }}>View</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}


export default ReviewQueue;
