const { useState, useCallback, useEffect, useRef } = React;

const API = "";

/* ── Toast system ── */
function ToastContainer({ toasts, onDismiss }) {
  return (
    <div className="toast-container">
      {toasts.map(t => (
        <div key={t.id} className={`toast ${t.type}`} onClick={() => onDismiss(t.id)}>
          {t.message}
        </div>
      ))}
    </div>
  );
}

/* ── ReconciliationTrace ── */
function ReconciliationTrace({ result, onBack }) {
  if (!result) return (
    <div className="card">
      <div className="empty-state">
        <div className="empty-icon">📋</div>
        <div className="empty-title">No settlement selected</div>
        <div className="empty-hint">Click a row in the results table to view its reconciliation trace.</div>
      </div>
    </div>
  );

  const ai = result.ai_response;
  const hasFailed = result.deterministic_checks_failed.length > 0;
  const isClean = result.decision_state === "CLEAN_MATCH";

  return (
    <div className="card">
      <div className="flex-between" style={{ marginBottom: 16 }}>
        <div>
          <button className="btn btn-sm" onClick={onBack}>&larr; Back to results</button>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {result.ai_mode === "demo" && (
            <span className="badge mock-tag">DETERMINISTIC DEMO</span>
          )}
          <span className={`badge ${isClean ? "clean" : ai ? "review" : "exception"}`}>
            {ai ? (result.ai_mode === "demo" ? "Deterministic Classification" : "AI Investigated") : isClean ? "Clean Match" : "Deterministic Rule"}
          </span>
        </div>
      </div>

      <h2 style={{ fontSize: "1.1rem", fontWeight: 700, marginBottom: 16, fontFamily: "'SF Mono', monospace" }}>
        {result.settlement_id}
      </h2>

      <div className="trace-box">
        <div><span className="t-label">Expected</span>    <span className="t-dim">  </span> <span className="t-good">{'\u20B9'}{(result.expected_amount_paise / 100).toLocaleString('en-IN')}</span> <span className="t-dim">{'\u2190'} Deterministic Engine</span></div>
        <div><span className="t-label">Actual</span>      <span className="t-dim">  </span> <span className={result.difference_paise !== 0 ? "t-bad" : "t-good"}>{'\u20B9'}{(result.actual_amount_paise / 100).toLocaleString('en-IN')}</span> <span className="t-dim">{'\u2190'} settlements.csv</span></div>
        <div className="trace-sep">{'\u2500'.repeat(48)}</div>
        <div><span className="t-label">Difference</span> <span className="t-dim"> </span> <span className={result.difference_paise === 0 ? "t-good" : "t-accent"}>{'\u20B9'}{(result.difference_paise / 100).toLocaleString('en-IN')}</span></div>
        <div><span className="t-label">Decision</span>   <span className="t-dim">  </span> <span className={isClean ? "t-good" : "t-accent"}>{result.decision_state}</span></div>
        <div className="trace-sep">{'\u2500'.repeat(48)}</div>
        {result.deterministic_checks_passed.length > 0 && (
          <div><span className="t-good">PASSED</span> <span className="t-dim">{result.deterministic_checks_passed.join('  ')}</span></div>
        )}
        {hasFailed && (
          <div><span className="t-bad">FAILED</span> <span className="t-dim">{result.deterministic_checks_failed.join('  ')}</span></div>
        )}
      </div>

      {ai && (
        <div className="ai-section">
          <h4>{result.ai_mode === "demo" ? "Deterministic Demo (Heuristic Classification)" : "AI Investigation"}</h4>
          <div className="trace-box" style={{ background: '#1e1b4b' }}>
            <div><span className="t-label">Classification</span>  <span className="t-dim"> </span> <span style={{color:'#c084fc'}}>{ai.classification}</span></div>
            <div><span className="t-label">Confidence</span>     <span className="t-dim">    </span> <span style={{color:'#c084fc'}}>{result.ai_mode === "demo" ? "Heuristic" : `${(ai.raw_confidence * 100).toFixed(0)}%`}</span></div>
            <div><span className="t-label">Action</span>         <span className="t-dim">       </span> <span style={{color:'#fbbf24'}}>{ai.recommended_action}</span></div>
            <div className="trace-sep">{'\u2500'.repeat(48)}</div>
            <div><span className="t-label">Explanation</span></div>
            <div style={{color:'#e2e8f0', marginTop: 4}}>{ai.explanation}</div>
            {ai.cited_evidence.length > 0 && (
              <div style={{marginTop: 8}}><span className="t-label">Evidence</span> <span className="t-good">{ai.cited_evidence.join(', ')}</span></div>
            )}
          </div>
        </div>
      )}

      {!ai && (
        <div style={{ marginTop: 16, padding: '12px 16px', background: 'var(--green-bg)', borderRadius: 'var(--radius)', border: '1px solid #bbf7d0', fontSize: '0.85rem', color: 'var(--green)' }}>
          <strong>Deterministic rules completely explain this settlement.</strong> AI investigation not required.
        </div>
      )}
    </div>
  );
}

/* ── App ── */
function App() {
  const [view, setView] = useState("upload");
  const [jobId, setJobId] = useState(null);
  const [status, setStatus] = useState(null);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [health, setHealth] = useState(null);
  const [toasts, setToasts] = useState([]);
  const toastId = useRef(0);

  const addToast = useCallback((message, type = "info") => {
    const id = ++toastId.current;
    setToasts(prev => [...prev, { id, message, type }]);
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), 4000);
  }, []);

  const dismissToast = useCallback((id) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  // Health check
  useEffect(() => {
    const check = async () => {
      try {
        const r = await fetch(`${API}/health`);
        setHealth(r.ok ? "ok" : "error");
      } catch { setHealth("error"); }
    };
    check();
    const interval = setInterval(check, 15000);
    return () => clearInterval(interval);
  }, []);

  const fetchStatus = useCallback(async (jid) => {
    setLoading(true);
    try {
      const resp = await fetch(`${API}/status/${jid}`);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (data.status === "error") {
        addToast(`Processing error: ${data.error || "Unknown error"}`, "error");
        setLoading(false);
        return;
      }
      setStatus(data);
      setView("dashboard");
      addToast(`Processed ${data.total_settlements} settlements`, "success");
    } catch (err) {
      addToast(`Failed to fetch results: ${err.message}`, "error");
    }
    setLoading(false);
  }, [addToast]);

  const onUploadComplete = useCallback((jid) => {
    setJobId(jid);
    fetchStatus(jid);
  }, [fetchStatus]);

  const resetAll = useCallback(() => {
    setView("upload");
    setJobId(null);
    setStatus(null);
    setSelected(null);
  }, []);

  const hasData = status && status.status === "completed";

  return (
    <div>
      <div className="header">
        <div style={{ display: 'flex', alignItems: 'baseline' }}>
          <h1>Nivara</h1>
          <span className="header-subtitle">Settlement Intelligence</span>
        </div>
        <div className="header-right">
          <span className="health-label">
            <span className={`health-dot ${health === "ok" ? "ok" : "err"}`} />
            {" "}API {health === "ok" ? "Connected" : "Unavailable"}
          </span>
          {hasData && (
            <button className="new-batch-btn" onClick={resetAll}>+ New Batch</button>
          )}
        </div>
      </div>

      <ToastContainer toasts={toasts} onDismiss={dismissToast} />

      <div className="container">
        <div className="tab-bar">
          <div className={`tab ${view === "upload" ? "active" : ""}`} onClick={() => setView("upload")}>Upload</div>
          <div className={`tab ${!hasData ? "disabled" : ""} ${view === "dashboard" ? "active" : ""}`} onClick={() => hasData && setView("dashboard")}>Dashboard</div>
          <div className={`tab ${!hasData ? "disabled" : ""} ${view === "trace" ? "active" : ""}`} onClick={() => hasData && setView("trace")}>Trace</div>
          <div className={`tab ${!hasData ? "disabled" : ""} ${view === "queue" ? "active" : ""}`} onClick={() => hasData && setView("queue")}>Review Queue</div>
          <div className={`tab ${!hasData ? "disabled" : ""} ${view === "patterns" ? "active" : ""}`} onClick={() => hasData && setView("patterns")}>Patterns</div>
          <div className={`tab ${!hasData ? "disabled" : ""} ${view === "audit" ? "active" : ""}`} onClick={() => hasData && setView("audit")}>Audit Trail</div>
        </div>

        {view === "upload" && <UploadPanel onUploadComplete={onUploadComplete} loading={loading} />}

        {loading && (
          <div className="card">
            <div className="loading-overlay">
              <div className="spinner" />
              Processing reconciliation...
            </div>
          </div>
        )}

        {view === "dashboard" && hasData && !loading && (
          <>
            <HeroMetrics status={status} />
            <ResultsTable
              results={status.results}
              selectedId={selected?.settlement_id}
              onSelect={(r) => { setSelected(r); setView("trace"); }}
            />
          </>
        )}

        {view === "trace" && (
          <ReconciliationTrace result={selected} onBack={() => setView("dashboard")} />
        )}

        {view === "queue" && hasData && !loading && (
          <ReviewQueue results={status.results} onSelect={(r) => { setSelected(r); setView("trace"); }} />
        )}

        {view === "patterns" && hasData && !loading && (
          <BatchPatterns patterns={status.batch_analysis} />
        )}

        {view === "audit" && hasData && !loading && (
          <AuditTrace auditRecords={status.audit_records} uploadHash={status.upload_hash} />
        )}
      </div>
    </div>
  );
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(<App />);
