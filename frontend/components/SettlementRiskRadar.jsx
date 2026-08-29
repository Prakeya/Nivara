function SettlementRiskRadar({ result }) {
  if (!result) return null;

  const dims = React.useMemo(() => {
    const diff = Math.abs(result.difference_paise || 0);
    const expected = result.expected_amount_paise || 1;
    const diffPct = Math.min(diff / expected, 1);

    const checks = result.deterministic_checks_failed || [];
    const feeRisk = checks.some(c => c.includes('FEE')) ? 0.7 + diffPct * 0.3 : diffPct * 0.4;
    const taxRisk = checks.some(c => c.includes('TAX')) ? 0.8 : diffPct * 0.2;
    const bankRisk = checks.some(c => c.includes('BANK')) ? 0.75 : (diff > 0 ? 0.3 + diffPct * 0.3 : 0.1);
    const refundRisk = checks.some(c => c.includes('REFUND')) ? 0.85 : 0.15;
    const linkageRisk = result.decision_state === "CLEAN_MATCH" ? 0.05 : 0.3 + diffPct * 0.5;

    return [
      { label: 'Fee Risk', value: Math.min(feeRisk, 1), angle: 0 },
      { label: 'Tax Risk', value: Math.min(taxRisk, 1), angle: 72 },
      { label: 'Bank Credit', value: Math.min(bankRisk, 1), angle: 144 },
      { label: 'Refund Risk', value: Math.min(refundRisk, 1), angle: 216 },
      { label: 'Linkage Risk', value: Math.min(linkageRisk, 1), angle: 288 },
    ];
  }, [result]);

  const size = 200;
  const cx = size / 2;
  const cy = size / 2;
  const maxR = size * 0.38;
  const levels = 5;

  const polarToXY = (angle, r) => {
    const rad = (angle - 90) * Math.PI / 180;
    return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
  };

  const levelPolygons = React.useMemo(() => {
    return Array.from({ length: levels }, (_, l) => {
      const r = (maxR / levels) * (l + 1);
      return dims.map(d => polarToXY(d.angle, r));
    });
  }, [dims]);

  const dataPolygon = React.useMemo(() => {
    return dims.map(d => polarToXY(d.angle, maxR * d.value));
  }, [dims]);

  const riskLevel = (v) => {
    if (v < 0.3) return { color: '#4ade80', label: 'Low' };
    if (v < 0.6) return { color: '#fbbf24', label: 'Medium' };
    if (v < 0.8) return { color: '#f97316', label: 'High' };
    return { color: '#ef4444', label: 'Critical' };
  };

  const overallRisk = dims.reduce((s, d) => s + d.value, 0) / dims.length;
  const overall = riskLevel(overallRisk);

  return (
    <div className="card" style={{ borderLeft: '3px solid #ef4444' }}>
      <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: '1.1rem' }}>&#127919;</span> Settlement Risk Radar
      </div>
      <div className="card-subtitle" style={{ marginBottom: 12 }}>
        Multi-dimensional risk assessment &bull; Overall: <span style={{ color: overall.color, fontWeight: 600 }}>{overall.label}</span>
      </div>

      <div style={{ display: 'flex', gap: 24, alignItems: 'center', flexWrap: 'wrap' }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ flexShrink: 0 }}>
          {/* Grid levels */}
          {levelPolygons.map((poly, l) => (
            <polygon
              key={l}
              points={poly.map(p => `${p.x},${p.y}`).join(' ')}
              fill="none"
              stroke="var(--border)"
              strokeWidth="1"
              opacity={0.5}
            />
          ))}

          {/* Axis lines */}
          {dims.map((d, i) => {
            const end = polarToXY(d.angle, maxR);
            return <line key={i} x1={cx} y1={cy} x2={end.x} y2={end.y} stroke="var(--border)" strokeWidth="1" opacity="0.4" />;
          })}

          {/* Data polygon */}
          <polygon
            points={dataPolygon.map(p => `${p.x},${p.y}`).join(' ')}
            fill={overall.color + '33'}
            stroke={overall.color}
            strokeWidth="2"
          />

          {/* Data points */}
          {dataPolygon.map((p, i) => (
            <circle key={i} cx={p.x} cy={p.y} r="4" fill={overall.color} stroke="#fff" strokeWidth="2" />
          ))}

          {/* Labels */}
          {dims.map((d, i) => {
            const labelPos = polarToXY(d.angle, maxR + 22);
            return (
              <text key={i} x={labelPos.x} y={labelPos.y} textAnchor="middle" dominantBaseline="middle"
                fontSize="9" fontWeight="600" fill="var(--text-secondary)">
                {d.label}
              </text>
            );
          })}
        </svg>

        <div style={{ flex: 1, minWidth: 180 }}>
          {dims.map((d, i) => {
            const rl = riskLevel(d.value);
            return (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', width: 75, textAlign: 'right' }}>{d.label}</span>
                <div style={{ flex: 1, height: 6, background: 'var(--border)', borderRadius: 3, overflow: 'hidden' }}>
                  <div style={{ width: `${d.value * 100}%`, height: '100%', background: rl.color, borderRadius: 3, transition: 'width 0.3s' }} />
                </div>
                <span style={{ fontSize: '0.72rem', fontWeight: 600, color: rl.color, width: 50 }}>{rl.label}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

window.SettlementRiskRadar = SettlementRiskRadar;
