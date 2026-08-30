function CrossSourceLinker({ status }) {
  if (!status || !status.results) return null;

  const results = status.results;

  const cleanCount = results.filter(r => r.decision_state === 'CLEAN_MATCH').length;
  const exceptionCount = results.length - cleanCount;

  const csvCounts = status.csv_counts || {};
  const sources = React.useMemo(() => {
    return [
      { name: 'transactions.csv', label: 'Transactions', color: '#2563eb', icon: '\uD83D\uDCC4', count: csvCounts.transactions || 0, desc: 'Internal transaction records' },
      { name: 'settlements.csv', label: 'Settlements', color: '#7c3aed', icon: '\uD83D\uDCB0', count: csvCounts.settlements || 0, desc: 'Settlement batch data' },
      { name: 'refunds.csv', label: 'Refunds', color: '#dc2626', icon: '\uD83D\uDCB8', count: csvCounts.refunds || 0, desc: 'Refund transaction records' },
      { name: 'bank_credits.csv', label: 'Bank Credits', color: '#059669', icon: '\uD83C\uDFE6', count: csvCounts.bank_credits || 0, desc: 'Bank credit statements' },
    ];
  }, [csvCounts.transactions, csvCounts.settlements, csvCounts.refunds, csvCounts.bank_credits]);

  const linkedPairs = React.useMemo(() => {
    const pairs = [];
    const sample = results.slice(0, Math.min(20, results.length));
    for (let i = 0; i < sample.length; i++) {
      const r = sample[i];
      // Transaction → Settlement link (every settlement has a transaction)
      if (i > 0) {
        pairs.push({ from: 0, to: 1, fromIdx: i, toIdx: i, color: r.decision_state === 'CLEAN_MATCH' ? '#4ade80' : '#fbbf24' });
      }
      // Settlement → Bank Credit link (when difference is non-zero, bank credit matters)
      if (r.difference_paise !== 0) {
        pairs.push({ from: 1, to: 3, fromIdx: i, toIdx: i, color: '#f87171' });
      }
      // Settlement → Refund link (only when refund-related checks failed)
      if (r.deterministic_checks_failed && r.deterministic_checks_failed.some(c => c.includes('refund'))) {
        pairs.push({ from: 1, to: 2, fromIdx: i, toIdx: i, color: '#94a3b8' });
      }
    }
    return pairs;
  }, [results]);

  const [hovered, setHovered] = React.useState(null);

  return (
    <div className="card" style={{ borderLeft: '3px solid #0ea5e9' }}>
      <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: '1.1rem' }}>&#128279;</span> Cross-Source Visual Linker
      </div>
      <div className="card-subtitle" style={{ marginBottom: 16 }}>
        How your 4 CSV sources connect &bull; {results.length} settlements cross-referenced
      </div>

      <div style={{ position: 'relative', padding: '0 8px' }}>
        <svg
          style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
          viewBox="0 0 800 160"
          preserveAspectRatio="none"
        >
          <defs>
            <linearGradient id="linkGradGood" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#4ade80" stopOpacity="0.6" />
              <stop offset="100%" stopColor="#4ade80" stopOpacity="0.3" />
            </linearGradient>
            <linearGradient id="linkGradWarn" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#fbbf24" stopOpacity="0.6" />
              <stop offset="100%" stopColor="#fbbf24" stopOpacity="0.3" />
            </linearGradient>
            <linearGradient id="linkGradBad" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stopColor="#f87171" stopOpacity="0.6" />
              <stop offset="100%" stopColor="#f87171" stopOpacity="0.3" />
            </linearGradient>
          </defs>
          {linkedPairs.slice(0, 12).map((p, i) => {
            const x1 = (p.from / 3) * 700 + 100;
            const x2 = (p.to / 3) * 700 + 100;
            const y1 = 30 + (p.fromIdx % 5) * 20;
            const y2 = 30 + (p.toIdx % 5) * 20;
            return (
              <path
                key={i}
                d={`M${x1},${y1} C${(x1+x2)/2},${y1} ${(x1+x2)/2},${y2} ${x2},${y2}`}
                fill="none"
                stroke={p.color}
                strokeWidth="1.5"
                opacity={hovered !== null && hovered !== i ? 0.15 : 0.5}
                style={{ transition: 'opacity 0.2s' }}
              />
            );
          })}
        </svg>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, position: 'relative', zIndex: 1 }}>
          {sources.map((src, i) => (
            <div
              key={i}
              style={{
                padding: '12px',
                borderRadius: 'var(--radius)',
                background: hovered === i ? src.color + '15' : 'var(--gray-bg)',
                border: `2px solid ${hovered === i ? src.color : 'var(--border)'}`,
                transition: 'all 0.15s',
                cursor: 'pointer'
              }}
              onMouseEnter={() => setHovered(i)}
              onMouseLeave={() => setHovered(null)}
            >
              <div style={{ fontSize: '1.4rem', marginBottom: 4 }}>{src.icon}</div>
              <div style={{ fontWeight: 700, fontSize: '0.82rem', color: src.color }}>{src.label}</div>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'monospace' }}>{src.name}</div>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', marginTop: 4 }}>{src.desc}</div>
              <div style={{ marginTop: 8, padding: '4px 8px', background: src.color + '15', borderRadius: 4, textAlign: 'center' }}>
                <div style={{ fontSize: '1rem', fontWeight: 700, color: src.color }}>{src.count}</div>
                <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Records</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div style={{ marginTop: 12, padding: '8px 12px', background: 'var(--blue-bg)', borderRadius: 'var(--radius)', fontSize: '0.78rem', color: 'var(--blue)', border: '1px solid #bfdbfe' }}>
        <strong>Linkage Status:</strong> {cleanCount} settlements matched cleanly across all 4 sources &bull; {exceptionCount} settlements have discrepancies requiring review
      </div>
    </div>
  );
}

window.CrossSourceLinker = CrossSourceLinker;
