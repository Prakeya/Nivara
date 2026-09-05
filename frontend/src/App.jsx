import React from 'react'
import ReactDOM from 'react-dom/client'
import './index.css'
import MetricsDashboard from './components/MetricsDashboard.jsx'
import UploadPanel from './components/UploadPanel.jsx'
import ResultsTable, { HeroMetrics } from './components/ResultsTable.jsx'
import CashFlowImpact from './components/CashFlowImpact.jsx'
import SettlementSimulator from './components/SettlementSimulator.jsx'
import AgentReasoningTree from './components/AgentReasoningTree.jsx'
import SettlementRiskRadar from './components/SettlementRiskRadar.jsx'
import CrossSourceLinker from './components/CrossSourceLinker.jsx'
import ReviewQueue from './components/ReviewQueue.jsx'
import BatchPatterns from './components/BatchPatterns.jsx'
import AuditTrace from './components/AuditTrace.jsx'
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

/* ── Blind Spot Modal ── */
function BlindSpotModal({ results, onClose }) {
  if (!results) return null;
  const blindSpots = results.filter(r => r.gt_label === "refund_after_settlement" || r.gt_label === "timing_race");
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
          <div>
            <div style={{ fontSize: '1rem', fontWeight: 700 }}>Blind Spot Settlements</div>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>{blindSpots.length} known false negatives &mdash; engine cannot detect these</div>
          </div>
          <button className="btn btn-sm" onClick={onClose}>&times;</button>
        </div>
        <div style={{ padding: '8px 12px', background: 'var(--red-bg)', borderRadius: 'var(--radius)', border: '1px solid #fecaca', fontSize: '0.8rem', color: 'var(--red)', marginBottom: 12 }}>
          These settlements appear clean to the deterministic engine but have exceptions in ground truth. They require live LLM investigation or additional business rules to catch.
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.82rem' }}>
          <thead>
            <tr>
              <th style={{ textAlign: 'left', padding: '6px 8px', borderBottom: '2px solid var(--border)', fontSize: '0.7rem', color: 'var(--text-muted)' }}>Settlement ID</th>
              <th style={{ textAlign: 'left', padding: '6px 8px', borderBottom: '2px solid var(--border)', fontSize: '0.7rem', color: 'var(--text-muted)' }}>Ground Truth Label</th>
              <th style={{ textAlign: 'right', padding: '6px 8px', borderBottom: '2px solid var(--border)', fontSize: '0.7rem', color: 'var(--text-muted)' }}>Expected</th>
              <th style={{ textAlign: 'right', padding: '6px 8px', borderBottom: '2px solid var(--border)', fontSize: '0.7rem', color: 'var(--text-muted)' }}>Actual</th>
            </tr>
          </thead>
          <tbody>
            {blindSpots.map((r, i) => (
              <tr key={r.settlement_id} style={{ borderBottom: '1px solid var(--border-light)' }}>
                <td style={{ padding: '6px 8px', fontFamily: 'monospace', fontWeight: 600 }}>{r.settlement_id}</td>
                <td style={{ padding: '6px 8px' }}>
                  <span className="badge" style={{ background: '#fef2f2', color: '#dc2626', border: '1px solid #fecaca' }}>
                    {r.gt_label === "refund_after_settlement" ? "Refund After Settlement" : "Timing Race"}
                  </span>
                </td>
                <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'monospace' }}>{'\u20B9'}{(r.expected_amount_paise / 100).toLocaleString('en-IN')}</td>
                <td style={{ padding: '6px 8px', textAlign: 'right', fontFamily: 'monospace' }}>{'\u20B9'}{(r.actual_amount_paise / 100).toLocaleString('en-IN')}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
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
  const [prevView, setPrevView] = useState("dashboard");
  const [jobId, setJobId] = useState(null);
  const [status, setStatus] = useState(null);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(false);
  const [health, setHealth] = useState(null);
  const [toasts, setToasts] = useState([]);
  const [bannerDismissed, setBannerDismissed] = useState(false);
  const [streamedIds, setStreamedIds] = useState(new Set());
  const [showBlindSpots, setShowBlindSpots] = useState(false);
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
    setStreamedIds(new Set());
  }, []);

  const handleHumanReview = useCallback(async (settlementId, decision) => {
    try {
      const resp = await fetch(
        `${API}/api/review/${settlementId}/decision`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            decision: decision,
            reason: "Manual review",
            reviewer_id: "frontend_user",
          }),
        }
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

      {showBlindSpots && status && (
        <BlindSpotModal results={status.results} onClose={() => setShowBlindSpots(false)} />
      )}

      {status && status.ai_mode === "demo" && !bannerDismissed && (
        <div style={{background:'#fef3c7', borderBottom:'1px solid #fcd34d', padding:'8px 24px', display:'flex', justifyContent:'space-between', alignItems:'center', fontSize:'0.85rem', color:'#92400e'}}>
          <span>
            <strong>Deterministic Demo Mode</strong> &mdash; heuristic AI classifications. Set <code style={{background:'#fde68a', padding:'1px 4px', borderRadius:3}}>GROQ_API_KEY</code> environment variable for live LLM investigation.
          </span>
          <button onClick={() => setBannerDismissed(true)} style={{background:'none', border:'none', color:'#92400e', cursor:'pointer', fontSize:'1.1rem', padding:'0 4px'}}>&times;</button>
        </div>
      )}

      <div className="container">
        <div className="tab-bar">
          <div className={`tab ${view === "upload" ? "active" : ""}`} onClick={() => setView("upload")}>Upload</div>
          <div className={`tab ${!hasData ? "disabled" : ""} ${view === "dashboard" ? "active" : ""}`} onClick={() => hasData && setView("dashboard")}>Dashboard</div>
          <div className={`tab ${!hasData ? "disabled" : ""} ${view === "trace" ? "active" : ""}`} onClick={() => hasData && setView("trace")}>Trace</div>
          <div className={`tab ${!hasData ? "disabled" : ""} ${view === "queue" ? "active" : ""}`} onClick={() => hasData && setView("queue")}>Review Queue</div>
          <div className={`tab ${!hasData ? "disabled" : ""} ${view === "patterns" ? "active" : ""}`} onClick={() => hasData && setView("patterns")}>Patterns</div>
          <div className={`tab ${!hasData ? "disabled" : ""} ${view === "sources" ? "active" : ""}`} onClick={() => hasData && setView("sources")}>Sources</div>
          <div className={`tab ${!hasData ? "disabled" : ""} ${view === "audit" ? "active" : ""}`} onClick={() => hasData && setView("audit")}>Audit Trail</div>
          <div className={`tab ${view === "metrics" ? "active" : ""}`} onClick={() => setView("metrics")}>Metrics</div>
        </div>

        {view === "metrics" && <MetricsDashboard />}

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
            <HeroMetrics status={status} onBlindSpotClick={() => setShowBlindSpots(true)} />
            <CashFlowImpact status={status} />
            <SettlementSimulator results={status.results} streamedIds={streamedIds} onStreamedIdsChange={setStreamedIds} />
            <ResultsTable
              results={status.results}
              selectedId={selected?.settlement_id}
              onSelect={(r) => { setSelected(r); setPrevView("dashboard"); setView("trace"); }}
              streamedIds={streamedIds}
            />
          </>
        )}

        {view === "trace" && (
          <>
            <ReconciliationTrace result={selected} onBack={() => setView(prevView)} onReview={handleHumanReview} />
            {selected && selected.agent_response && selected.agent_response.trace && selected.agent_response.trace.steps && selected.agent_response.trace.steps.length > 0 && (
              <AgentReasoningTree agentResponse={selected.agent_response} />
            )}
            {selected && <SettlementRiskRadar result={selected} />}
          </>
        )}

        {view === "sources" && hasData && !loading && (
          <CrossSourceLinker status={status} />
        )}

        {view === "queue" && hasData && !loading && (
          <ReviewQueue results={status.results} onSelect={(r) => { setSelected(r); setPrevView("queue"); setView("trace"); }} onReview={handleHumanReview} />
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

export default App;
