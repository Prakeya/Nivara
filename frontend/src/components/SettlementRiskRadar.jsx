import React from 'react';
function SettlementRiskRadar({ result }) {
  if (!result) return null;

  const dims = React.useMemo(() => {
    const checks = result.deterministic_checks_failed || [];
    const aiClass = result.ai_response ? result.ai_response.classification : null;
    const diff = Math.abs(result.difference_paise || 0);
    const expected = result.expected_amount_paise || 1;
    const diffPct = Math.min(diff / expected, 1);
    const isClean = result.decision_state === "CLEAN_MATCH";

    let feeRisk, taxRisk, bankRisk, refundRisk, linkageRisk;

    if (isClean) {
      feeRisk = 0.05; taxRisk = 0.05; bankRisk = 0.05; refundRisk = 0.05; linkageRisk = 0.05;
    } else if (checks.includes("fee_validation")) {
      feeRisk = 0.95; taxRisk = 0.15; bankRisk = 0.2; refundRisk = 0.1; linkageRisk = 0.2;
    } else if (checks.includes("tax_validation")) {
      feeRisk = 0.2; taxRisk = 0.95; bankRisk = 0.15; refundRisk = 0.1; linkageRisk = 0.2;
    } else if (checks.includes("bank_credit_existence")) {
      feeRisk = 0.1; taxRisk = 0.1; bankRisk = 0.95; refundRisk = 0.15; linkageRisk = 0.3;
    } else if (checks.includes("duplicate_detection")) {
      feeRisk = 0.1; taxRisk = 0.1; bankRisk = 0.15; refundRisk = 0.1; linkageRisk = 0.95;
    } else if (checks.includes("reference_existence")) {
      feeRisk = 0.15; taxRisk = 0.15; bankRisk = 0.2; refundRisk = 0.15; linkageRisk = 0.9;
    } else if (aiClass === "REFUND_TIMING") {
      feeRisk = 0.1; taxRisk = 0.1; bankRisk = 0.15; refundRisk = 0.85; linkageRisk = 0.2;
    } else if (aiClass === "TIMING_RACE") {
      feeRisk = 0.1; taxRisk = 0.1; bankRisk = 0.7; refundRisk = 0.7; linkageRisk = 0.15;
    } else if (aiClass === "REFUND_AFTER_SETTLEMENT") {
      feeRisk = 0.1; taxRisk = 0.1; bankRisk = 0.15; refundRisk = 0.95; linkageRisk = 0.15;
    } else if (aiClass === "PARTIAL_SETTLEMENT") {
      feeRisk = 0.1; taxRisk = 0.1; bankRisk = 0.85; refundRisk = 0.15; linkageRisk = 0.2;
    } else {
      // Unexplained / unknown: medium across all
      feeRisk = 0.45; taxRisk = 0.45; bankRisk = 0.45; refundRisk = 0.45; linkageRisk = 0.45;
    }

    return [
      { label: 'Fee Risk', value: feeRisk, angle: 0 },
      { label: 'Tax Risk', value: taxRisk, angle: 72 },
      { label: 'Bank Credit', value: bankRisk, angle: 144 },
      { label: 'Refund Risk', value: refundRisk, angle: 216 },
      { label: 'Linkage Risk', value: linkageRisk, angle: 288 },
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
    if (v < 0.3) return { color: '#2f6f52', label: 'Low' };
    if (v < 0.6) return { color: '#a16a1f', label: 'Medium' };
    if (v < 0.8) return { color: '#b8532a', label: 'High' };
    return { color: '#9c3b32', label: 'Critical' };
  };

  const overallRisk = dims.reduce((s, d) => s + d.value, 0) / dims.length;
  const overall = riskLevel(overallRisk);

  return (
    <div className="card" style={{ borderLeft: '3px solid #9c3b32' }}>
      <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: '1.1rem' }}>&#127919;</span> Settlement Risk Radar
      </div>
      <div className="card-subtitle" style={{ marginBottom: 12 }}>
        Multi-dimensional risk assessment &bull; Overall: <span style={{ color: overall.color, fontWeight: 600 }}>{overall.label}</span>
      </div>

      <div style={{ display: 'flex', gap: 24, alignItems: 'center', flexWrap: 'wrap' }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ flexShrink: 0 }}>
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
          {dims.map((d, i) => {
            const end = polarToXY(d.angle, maxR);
            return <line key={i} x1={cx} y1={cy} x2={end.x} y2={end.y} stroke="var(--border)" strokeWidth="1" opacity="0.4" />;
          })}
          <polygon
            points={dataPolygon.map(p => `${p.x},${p.y}`).join(' ')}
            fill={overall.color + '33'}
            stroke={overall.color}
            strokeWidth="2"
          />
          {dataPolygon.map((p, i) => (
            <circle key={i} cx={p.x} cy={p.y} r="4" fill={overall.color} stroke="#fff8ee" strokeWidth="2" />
          ))}
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


export default SettlementRiskRadar;
