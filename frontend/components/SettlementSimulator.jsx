function SettlementSimulator({ results }) {
  const [running, setRunning] = React.useState(false);
  const [paused, setPaused] = React.useState(false);
  const [streamed, setStreamed] = React.useState([]);
  const [progress, setProgress] = React.useState(0);
  const idxRef = React.useRef(0);
  const timerRef = React.useRef(null);

  const total = results ? results.length : 0;

  const start = () => {
    setRunning(true);
    setPaused(false);
    setStreamed([]);
    idxRef.current = 0;
    setProgress(0);
    tick();
  };

  const tick = () => {
    timerRef.current = setInterval(() => {
      if (idxRef.current >= total) {
        clearInterval(timerRef.current);
        setRunning(false);
        return;
      }
      idxRef.current++;
      setStreamed(prev => [...prev, results[idxRef.current - 1]]);
      setProgress((idxRef.current / total) * 100);
    }, 200);
  };

  const pause = () => {
    if (paused) {
      tick();
      setPaused(false);
    } else {
      clearInterval(timerRef.current);
      setPaused(true);
    }
  };

  const reset = () => {
    clearInterval(timerRef.current);
    setRunning(false);
    setPaused(false);
    setStreamed([]);
    setProgress(0);
    idxRef.current = 0;
  };

  React.useEffect(() => () => clearInterval(timerRef.current), []);

  const formatCurrency = (p) => '\u20B9' + (p / 100).toLocaleString('en-IN');

  const decisionColor = (d) => {
    if (d === "CLEAN_MATCH") return "#4ade80";
    if (d === "DETERMINISTIC_EXCEPTION") return "#f87171";
    if (d === "MATH_DISCREPANCY") return "#fbbf24";
    return "#94a3b8";
  };

  return (
    <div className="card" style={{ borderLeft: '3px solid #7c3aed' }}>
      <div className="flex-between" style={{ marginBottom: 12 }}>
        <div>
          <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontSize: '1.1rem' }}>&#9654;</span> Real-Time Settlement Simulator
          </div>
          <div className="card-subtitle">Watch settlements stream in at live-feed speed (200ms intervals)</div>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          {!running ? (
            <button className="btn btn-primary btn-sm" onClick={start} disabled={total === 0}>
              &#9654; Simulate Live Feed
            </button>
          ) : (
            <>
              <button className="btn btn-sm" onClick={pause}>{paused ? '&#9654; Resume' : '&#9646;&#9646; Pause'}</button>
              <button className="btn btn-sm btn-danger" onClick={reset}>&#8634; Reset</button>
            </>
          )}
        </div>
      </div>

      {(running || streamed.length > 0) && (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 4 }}>
            <span>{streamed.length} / {total} streamed</span>
            <span>{progress.toFixed(0)}%</span>
          </div>
          <div style={{ width: '100%', height: 6, background: 'var(--border)', borderRadius: 3, overflow: 'hidden', marginBottom: 12 }}>
            <div style={{ width: `${progress}%`, height: '100%', background: 'linear-gradient(90deg, #7c3aed, #2563eb)', borderRadius: 3, transition: 'width 0.15s ease' }} />
          </div>

          <div style={{ maxHeight: 260, overflowY: 'auto', fontFamily: "'SF Mono', monospace", fontSize: '0.78rem' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead>
                <tr>
                  <th style={{ textAlign: 'left', padding: '4px 8px', fontSize: '0.7rem', color: 'var(--text-muted)', position: 'sticky', top: 0, background: 'var(--surface)' }}>Settlement</th>
                  <th style={{ textAlign: 'right', padding: '4px 8px', fontSize: '0.7rem', color: 'var(--text-muted)', position: 'sticky', top: 0, background: 'var(--surface)' }}>Expected</th>
                  <th style={{ textAlign: 'right', padding: '4px 8px', fontSize: '0.7rem', color: 'var(--text-muted)', position: 'sticky', top: 0, background: 'var(--surface)' }}>Actual</th>
                  <th style={{ textAlign: 'right', padding: '4px 8px', fontSize: '0.7rem', color: 'var(--text-muted)', position: 'sticky', top: 0, background: 'var(--surface)' }}>Diff</th>
                  <th style={{ textAlign: 'center', padding: '4px 8px', fontSize: '0.7rem', color: 'var(--text-muted)', position: 'sticky', top: 0, background: 'var(--surface)' }}>Decision</th>
                </tr>
              </thead>
              <tbody>
                {streamed.map((r, i) => (
                  <tr key={i} style={{ animation: 'fadeSlideIn 0.2s ease-out', borderBottom: '1px solid var(--border-light)' }}>
                    <td style={{ padding: '4px 8px', fontWeight: 600 }}>{r.settlement_id}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right' }}>{formatCurrency(r.expected_amount_paise)}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right', color: r.difference_paise !== 0 ? 'var(--orange)' : 'var(--green)' }}>{formatCurrency(r.actual_amount_paise)}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'right', fontWeight: 600, color: r.difference_paise !== 0 ? '#f87171' : '#4ade80' }}>{formatCurrency(r.difference_paise)}</td>
                    <td style={{ padding: '4px 8px', textAlign: 'center' }}>
                      <span style={{ display: 'inline-block', padding: '1px 6px', borderRadius: 999, fontSize: '0.65rem', fontWeight: 600, background: decisionColor(r.decision_state) + '22', color: decisionColor(r.decision_state) }}>
                        {r.decision_state.replace(/_/g, ' ')}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {!running && streamed.length === 0 && (
        <div style={{ textAlign: 'center', padding: '20px 0', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          Click <strong>"Simulate Live Feed"</strong> to watch {total} settlements stream in at real-time speed
        </div>
      )}

      {running === false && streamed.length === total && total > 0 && (
        <div style={{ marginTop: 8, padding: '8px 12px', background: 'var(--green-bg)', borderRadius: 'var(--radius)', fontSize: '0.8rem', color: 'var(--green)', border: '1px solid #bbf7d0' }}>
          &#10003; Simulation complete &mdash; {total} settlements streamed
        </div>
      )}
    </div>
  );
}

window.SettlementSimulator = SettlementSimulator;
