import React from 'react';
function SettlementSimulator({ results, streamedIds, onStreamedIdsChange }) {
  const [running, setRunning] = React.useState(false);
  const [paused, setPaused] = React.useState(false);
  const [progress, setProgress] = React.useState(0);
  const idxRef = React.useRef(0);
  const timerRef = React.useRef(null);

  const total = results ? results.length : 0;

  const start = () => {
    setRunning(true);
    setPaused(false);
    onStreamedIdsChange(new Set());
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
      onStreamedIdsChange(prev => {
        const next = new Set(prev);
        next.add(results[idxRef.current - 1].settlement_id);
        return next;
      });
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
    onStreamedIdsChange(new Set());
    setProgress(0);
    idxRef.current = 0;
  };

  React.useEffect(() => () => clearInterval(timerRef.current), []);

  const streamedCount = streamedIds.size;

  return (
    <div className="card" style={{ borderLeft: '3px solid var(--purple)' }}>
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
              <button className="btn btn-sm" onClick={pause}>{paused ? '\u25B6 Resume' : '\u23F8 Pause'}</button>
              <button className="btn btn-sm btn-danger" onClick={reset}>&#8634; Reset</button>
            </>
          )}
        </div>
      </div>

      {(running || streamedCount > 0) && (
        <>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 4 }}>
            <span>{streamedCount} / {total} streamed</span>
            <span>{progress.toFixed(0)}%</span>
          </div>
          <div style={{ width: '100%', height: 6, background: 'var(--border)', borderRadius: 3, overflow: 'hidden', marginBottom: 12 }}>
            <div style={{ width: `${progress}%`, height: '100%', background: 'linear-gradient(90deg, var(--purple), var(--blue))', borderRadius: 3, transition: 'width 0.15s ease' }} />
          </div>
        </>
      )}

      {!running && streamedCount === 0 && (
        <div style={{ textAlign: 'center', padding: '20px 0', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          Click <strong>"Simulate Live Feed"</strong> to watch {total} settlements stream in at real-time speed
        </div>
      )}

      {running === false && streamedCount === total && total > 0 && (
        <div style={{ marginTop: 8, padding: '8px 12px', background: 'var(--green-bg)', borderRadius: 'var(--radius)', fontSize: '0.8rem', color: 'var(--green)', border: '1px solid var(--green)' }}>
          &#10003; Simulation complete &mdash; {total} settlements streamed
        </div>
      )}
    </div>
  );
}


export default SettlementSimulator;
