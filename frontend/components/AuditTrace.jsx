function AuditTrace({ auditRecords, uploadHash }) {
  const [expanded, setExpanded] = React.useState(null);

  if (!auditRecords || auditRecords.length === 0) return (
    <div className="card">
      <div className="empty-state">
        <div className="empty-icon">📋</div>
        <div className="empty-title">No audit records</div>
        <div className="empty-hint">Upload data to generate an audit trail.</div>
      </div>
    </div>
  );

  const badgeClass = (state) => {
    if (state === "CLEAN_MATCH") return "clean";
    if (state === "DETERMINISTIC_EXCEPTION") return "exception";
    if (state === "MATH_DISCREPANCY") return "math";
    if (state === "REVIEW_REQUIRED") return "review";
    return "unresolved";
  };

  return (
    <div className="card">
      <div className="audit-header">
        <div>
          <div className="card-title">Audit Trail</div>
          <div className="card-subtitle">{auditRecords.length} append-only records</div>
        </div>
        <div className="audit-tag">hash: {uploadHash}</div>
      </div>

      <div style={{ padding: '10px 14px', background: 'var(--blue-bg)', borderRadius: 'var(--radius)', border: '1px solid #bfdbfe', fontSize: '0.8rem', color: 'var(--blue)', marginBottom: 16 }}>
        This audit trail is append-only. Records cannot be updated or deleted.
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Settlement ID</th>
              <th>Decision</th>
              <th>Timestamp</th>
              <th style={{textAlign:'center'}}>Details</th>
            </tr>
          </thead>
          <tbody>
            {auditRecords.map((r, i) => (
              <React.Fragment key={r.id || i}>
                <tr>
                  <td className="sid">{r.settlement_id}</td>
                  <td><span className={`badge ${badgeClass(r.decision_state)}`}>{r.decision_state}</span></td>
                  <td style={{fontSize:'0.8rem', color:'var(--text-secondary)', fontFamily:'monospace'}}>{r.timestamp}</td>
                  <td style={{textAlign:'center'}}>
                    <button
                      className="btn btn-sm"
                      onClick={() => setExpanded(expanded === i ? null : i)}
                    >
                      {expanded === i ? "Hide" : "Show"} JSON
                    </button>
                  </td>
                </tr>
                {expanded === i && (
                  <tr>
                    <td colSpan="4" style={{padding:'0 14px 14px'}}>
                      <div className="audit-json">{JSON.stringify(JSON.parse(r.payload_json), null, 2)}</div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

window.AuditTrace = AuditTrace;
