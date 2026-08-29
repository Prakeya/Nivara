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
function ReconciliationTrace({ result, onBack, onReview }) {
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
  const agent = result.agent_response;
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
            {ai ? (result.ai_mode === "demo" ? "Deterministic Classification" : "LLM Classified") : isClean ? "Clean Match" : "Deterministic Rule"}
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
        {result.resolution_status && result.resolution_status !== "OPEN" && (
          <div><span className="t-label">Resolution</span> <span className="t-dim"> </span> <span style={{color:'#c084fc'}}>{result.resolution_status}</span></div>
        )}
        {result.agent_iterations > 0 && (
          <div><span className="t-label">Agent Iterations</span> <span className="t-dim"> </span> <span style={{color:'#60a5fa'}}>{result.agent_iterations}</span></div>
        )}
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
          <h4>{result.ai_mode === "demo" ? "Deterministic Demo (Heuristic Classification)" : "Exception Analysis"}</h4>
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
          <strong>Deterministic rules completely explain this settlement.</strong> No exception analysis needed.
        </div>
      )}

      {/* Agent Trace Section */}
      {agent && agent.trace && agent.trace.steps && agent.trace.steps.length > 0 && (
        <div className="ai-section" style={{ marginTop: 16 }}>
          <h4>Agent Reasoning Trace</h4>
          <div className="trace-box" style={{ background: '#0f172a' }}>
            {agent.trace.steps.map((step, idx) => (
              <div key={idx} style={{ marginBottom: 8, paddingBottom: 8, borderBottom: idx < agent.trace.steps.length - 1 ? '1px solid #1e293b' : 'none' }}>
                <div style={{display:'flex', alignItems:'center', gap: 8, marginBottom: 4}}>
                  <span className="badge" style={{
                    background: step.action_type === 'TOOL_CALL' ? '#1e40af' : step.action_type === 'DECISION' ? '#7c3aed' : '#374151',
                    color: '#fff', fontSize: '0.7rem', padding: '2px 6px'
                  }}>{step.action_type}</span>
                  <span style={{color:'#94a3b8', fontSize: '0.75rem'}}>Step {step.step_number}</span>
                </div>
                <div style={{color:'#e2e8f0', fontSize: '0.85rem'}}>{step.thought}</div>
                {step.tool_name && (
                  <div style={{color:'#60a5fa', fontSize: '0.8rem', marginTop: 2}}>
                    Tool: {step.tool_name}
                    {step.tool_args && Object.keys(step.tool_args).length > 0 && (
                      <span style={{color:'#94a3b8'}}> ({JSON.stringify(step.tool_args)})</span>
                    )}
                  </div>
                )}
                {step.tool_result && (
                  <div style={{color:'#4ade80', fontSize: '0.8rem', marginTop: 2}}>
                    Result: {step.tool_result.substring(0, 200)}
                  </div>
                )}
              </div>
            ))}
            <div className="trace-sep">{'\u2500'.repeat(48)}</div>
            <div><span className="t-label">Total Iterations</span> <span className="t-dim"> </span> <span style={{color:'#60a5fa'}}>{agent.trace.iteration_count}</span></div>
            <div><span className="t-label">Tool Calls</span> <span className="t-dim">      </span> <span style={{color:'#60a5fa'}}>{agent.tool_calls_made}</span></div>
            {agent.trace.self_corrections > 0 && (
              <div><span className="t-label">Self-Corrections</span> <span className="t-dim"> </span> <span style={{color:'#fbbf24'}}>{agent.trace.self_corrections}</span></div>
            )}
          </div>
        </div>
      )}

      {/* Human Review Action */}
      {result.escalate_to_human && result.decision_state !== "CLEAN_MATCH" && onReview && (
        <div style={{ marginTop: 16, padding: '12px 16px', background: 'var(--orange-bg)', borderRadius: 'var(--radius)', border: '1px solid #fde68a' }}>
          <div style={{ fontSize: '0.85rem', color: 'var(--orange)', marginBottom: 8, fontWeight: 600 }}>Human Review Required</div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn" style={{background: '#16a34a', color: '#fff'}} onClick={() => onReview(result.settlement_id, 'APPROVE')}>Approve</button>
            <button className="btn" style={{background: '#dc2626', color: '#fff'}} onClick={() => onReview(result.settlement_id, 'REJECT')}>Reject</button>
          </div>
        </div>
      )}

      {result.human_review && (
        <div style={{ marginTop: 16, padding: '12px 16px', background: '#f0fdf4', borderRadius: 'var(--radius)', border: '1px solid #bbf7d0', fontSize: '0.85rem' }}>
          <div style={{ fontWeight: 600, color: '#166534', marginBottom: 4 }}>Human Decision: {result.human_review.decision}</div>
          <div style={{ color: '#166534' }}>Reason: {result.human_review.reason}</div>
          <div style={{ color: '#6b7280', fontSize: '0.8rem' }}>Reviewer: {result.human_review.reviewer_id}</div>
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

  const handleHumanReview = useCallback(async (settlementId, decision) => {
    try {
      const resp = await fetch(
        `${API}/api/review/${settlementId}/decision?decision=${decision}&reason=Manual+review&reviewer_id=frontend_user`,
        { method: "POST" }
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();

      // Update local state
      if (status) {
        const updatedResults = status.results.map(r => {
          if (r.settlement_id === settlementId) {
            return {
              ...r,
              resolution_status: decision === "REJECT" ? "REJECTED" : "RESOLVED_BY_HUMAN",
              human_review: {
                decision: decision,
                reason: "Manual review",
                reviewer_id: "frontend_user",
                timestamp: data.timestamp,
              },
            };
          }
          return r;
        });
        setStatus({ ...status, results: updatedResults });

        if (selected && selected.settlement_id === settlementId) {
          setSelected({
            ...selected,
            resolution_status: decision === "REJECT" ? "REJECTED" : "RESOLVED_BY_HUMAN",
            human_review: {
              decision: decision,
              reason: "Manual review",
              reviewer_id: "frontend_user",
              timestamp: data.timestamp,
            },
          });
        }
      }

      addToast(`Settlement ${settlementId} ${decision.toLowerCase()}`, "success");
    } catch (err) {
      addToast(`Failed to submit review: ${err.message}`, "error");
    }
  }, [status, selected, addToast]);

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
          <ReconciliationTrace result={selected} onBack={() => setView("dashboard")} onReview={handleHumanReview} />
        )}

        {view === "queue" && hasData && !loading && (
          <ReviewQueue results={status.results} onSelect={(r) => { setSelected(r); setView("trace"); }} onReview={handleHumanReview} />
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
