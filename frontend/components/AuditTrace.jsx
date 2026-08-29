function AuditTrace({ auditRecords, uploadHash }) {
  const [expanded, setExpanded] = React.useState(null);
  const [verifyResult, setVerifyResult] = React.useState(null);
  const [verifyLoading, setVerifyLoading] = React.useState(false);

  const handleVerify = async () => {
    setVerifyLoading(true);
    setVerifyResult(null);
    try {
      const resp = await fetch(`/audit/${uploadHash}/verify`);
      const data = await resp.json();
      setVerifyResult(data);
    } catch (err) {
      setVerifyResult({ valid: false, error: err.message });
    }
    setVerifyLoading(false);
  };

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
          <div className="card-subtitle">{auditRecords.length} append-only records &bull; SHA-256 hash chain</div>
        </div>
        <div style={{display:'flex', alignItems:'center', gap: 8}}>
          <div className="audit-tag">hash: {uploadHash.substring(0, 16)}...</div>
          <button
            className="btn btn-sm btn-primary"
            onClick={handleVerify}
            disabled={verifyLoading}
            style={{fontSize: '0.8rem'}}
          >
            {verifyLoading ? "Verifying..." : "Verify Integrity"}
          </button>
        </div>
      </div>

      {verifyResult && (
        <div style={{
          padding: '12px 16px',
          borderRadius: 'var(--radius)',
          border: `1px solid ${verifyResult.valid ? '#bbf7d0' : '#fecaca'}`,
          background: verifyResult.valid ? 'var(--green-bg)' : 'var(--red-bg)',
          fontSize: '0.85rem',
          color: verifyResult.valid ? 'var(--green)' : 'var(--red)',
          marginBottom: 16,
          fontWeight: 500,
        }}>
          {verifyResult.valid
            ? <span>&#10003; Hash chain verified &mdash; {verifyResult.total_records} records, zero tampering detected</span>
            : <span>&#10007; Chain integrity failed at record {verifyResult.broken_at} ({verifyResult.settlement_id || 'unknown'})</span>
          }
        </div>
      )}

      <div style={{ padding: '10px 14px', background: 'var(--blue-bg)', borderRadius: 'var(--radius)', border: '1px solid #bfdbfe', fontSize: '0.8rem', color: 'var(--blue)', marginBottom: 16 }}>
        This audit trail is append-only. Records cannot be updated or deleted. Each record includes a SHA-256 hash of its payload plus the previous record's hash, creating a tamper-evident chain.
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
