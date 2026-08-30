function CashFlowImpact({ status }) {
  if (!status || !status.results) return null;

  const data = React.useMemo(() => {
    const results = status.results;
    const expected = results.reduce((s, r) => s + r.expected_amount_paise, 0);
    const actual = results.reduce((s, r) => s + r.actual_amount_paise, 0);
    const discrepancy = Math.abs(expected - actual);
    const clean = results.filter(r => r.decision_state === 'CLEAN_MATCH');
    const exceptions = results.filter(r => r.decision_state !== 'CLEAN_MATCH');

    // Recovery from caught exceptions only (not blind spots which have diff=0)
    const potentialRecovery = exceptions.reduce((s, r) => s + Math.abs(r.difference_paise), 0);
    const recoveryRate = discrepancy > 0 ? (potentialRecovery / discrepancy * 100) : 0;

    return {
      expected, actual, discrepancy, potentialRecovery,
      cleanCount: clean.length,
      exceptionCount: exceptions.length,
      recoveryRate,
    };
  }, [status]);

  const formatCurrency = (p) => '\u20B9' + Math.abs(p / 100).toLocaleString('en-IN', { maximumFractionDigits: 0 });

  const metrics = [
    { label: 'Expected Total', value: data.expected, color: '#2563eb', bg: '#eff6ff', icon: '&#128176;' },
    { label: 'Actual Credited', value: data.actual, color: '#059669', bg: '#ecfdf5', icon: '&#9989;' },
    { label: 'Discrepancy', value: data.discrepancy, color: data.discrepancy > 0 ? '#dc2626' : '#059669', bg: data.discrepancy > 0 ? '#fef2f2' : '#ecfdf5', icon: '&#9888;&#65039;', absolute: true },
    { label: 'Potential Recovery', value: data.potentialRecovery, color: '#d97706', bg: '#fffbeb', icon: '&#128200;' },
  ];

  return (
    <div className="card" style={{ borderLeft: '3px solid #059669' }}>
      <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: '1.1rem' }}>&#128202;</span> Cash Flow Impact Dashboard
      </div>
      <div className="card-subtitle" style={{ marginBottom: 16 }}>
        Monthly financial overview &bull; {status.total_settlements} settlements &bull; Recovery potential: {data.recoveryRate.toFixed(1)}%
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
        {metrics.map((m, i) => (
          <div key={i} style={{
            padding: '12px',
            borderRadius: 'var(--radius)',
            background: m.bg,
            border: `1px solid ${m.color}33`,
            textAlign: 'center'
          }}>
            <div style={{ fontSize: '1.2rem', marginBottom: 2 }} dangerouslySetInnerHTML={{ __html: m.icon }} />
            <div style={{ fontSize: '1.1rem', fontWeight: 700, color: m.color, fontFamily: "'SF Mono', monospace" }}>
              {formatCurrency(m.absolute ? m.value : m.value)}
            </div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em', marginTop: 2 }}>
              {m.label}
            </div>
          </div>
        ))}
      </div>

      {/* Visual bar comparison */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.75rem', color: 'var(--text-muted)', marginBottom: 4 }}>
          <span>Expected vs Actual</span>
          <span>{data.cleanCount} clean / {data.exceptionCount} exceptions</span>
        </div>
        <div style={{ display: 'flex', gap: 4, height: 24 }}>
          <div style={{ flex: data.expected, background: '#2563eb', borderRadius: '4px 0 0 4px', display: 'flex', alignItems: 'center', paddingLeft: 8, fontSize: '0.7rem', color: '#fff', fontWeight: 600, minWidth: 60, transition: 'flex 0.3s' }}>
            {formatCurrency(data.expected)}
          </div>
          <div style={{ flex: data.actual, background: '#059669', borderRadius: '0 4px 4px 0', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.7rem', color: '#fff', fontWeight: 600, minWidth: 60, transition: 'flex 0.3s' }}>
            {formatCurrency(data.actual)}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 16, marginTop: 4, fontSize: '0.72rem' }}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ width: 8, height: 8, borderRadius: 2, background: '#2563eb', display: 'inline-block' }} /> Expected</span>
          <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ width: 8, height: 8, borderRadius: 2, background: '#059669', display: 'inline-block' }} /> Actual</span>
          {data.discrepancy > 0 && (
            <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ width: 8, height: 8, borderRadius: 2, background: '#dc2626', display: 'inline-block' }} /> Gap: {formatCurrency(data.discrepancy)}</span>
          )}
        </div>
      </div>

      {/* Recovery insight */}
      <div style={{ padding: '10px 14px', background: data.potentialRecovery > 0 ? 'var(--orange-bg)' : 'var(--green-bg)', borderRadius: 'var(--radius)', border: `1px solid ${data.potentialRecovery > 0 ? '#fde68a' : '#bbf7d0'}`, fontSize: '0.82rem', color: data.potentialRecovery > 0 ? 'var(--orange)' : 'var(--green)' }}>
        {data.potentialRecovery > 0
          ? <span>&#128200; <strong>{formatCurrency(data.potentialRecovery)}</strong> in discrepancies identified across {data.exceptionCount} settlements. Nivara's AI investigated each, generating structured evidence for human resolution.</span>
          : <span>&#10003; All settlements matched perfectly. No cash flow discrepancies detected.</span>
        }
      </div>
    </div>
  );
}

window.CashFlowImpact = CashFlowImpact;
