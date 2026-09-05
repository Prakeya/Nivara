import React from 'react';
function AgentReasoningTree({ agentResponse }) {
  const [expandedSteps, setExpandedSteps] = React.useState({});
  const [expandedThoughts, setExpandedThoughts] = React.useState({});

  if (!agentResponse || !agentResponse.trace || !agentResponse.trace.steps || agentResponse.trace.steps.length === 0) {
    return (
      <div className="card" style={{ borderLeft: '3px solid var(--text-muted)' }}>
        <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <span style={{ fontSize: '1.1rem' }}>&#129504;</span> Agent Reasoning Tree
        </div>
        <div style={{ textAlign: 'center', padding: '16px 0', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
          No agent reasoning trace available for this settlement.
        </div>
      </div>
    );
  }

  const steps = agentResponse.trace.steps;
  const toggleStep = (i) => setExpandedSteps(prev => ({ ...prev, [i]: !prev[i] }));
  const toggleThought = (i) => setExpandedThoughts(prev => ({ ...prev, [i]: !prev[i] }));

  const stepIcon = (type) => {
    if (type === 'TOOL_CALL') return '\uD83D\uDD27';
    if (type === 'DECISION') return '\uD83D\uDD0D';
    if (type === 'OBSERVATION') return '\uD83D\uDC41';
    return '\u270F';
  };

  const stepColor = (type) => {
    if (type === 'TOOL_CALL') return '#2f5f6f';
    if (type === 'DECISION') return '#5b4a8a';
    if (type === 'OBSERVATION') return '#2f6f52';
    return '#9a8f78';
  };

  const stepBg = (type) => {
    if (type === 'TOOL_CALL') return 'var(--blue-bg)';
    if (type === 'DECISION') return 'var(--purple-bg)';
    if (type === 'OBSERVATION') return 'var(--green-bg)';
    return 'var(--gray-bg)';
  };

  return (
    <div className="card" style={{ borderLeft: '3px solid var(--purple)' }}>
      <div className="card-title" style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: '1.1rem' }}>&#129504;</span> Agent Reasoning Tree
      </div>
      <div className="card-subtitle" style={{ marginBottom: 12 }}>
        ReAct loop &mdash; {steps.length} steps &bull; {agentResponse.tool_calls_made} tool call{agentResponse.tool_calls_made !== 1 ? 's' : ''}
        {agentResponse.trace.self_corrections > 0 && <span> &bull; {agentResponse.trace.self_corrections} self-correction{agentResponse.trace.self_corrections !== 1 ? 's' : ''}</span>}
      </div>

      <div style={{ position: 'relative', paddingLeft: 20 }}>
        {/* Vertical connector line */}
        <div style={{ position: 'absolute', left: 7, top: 0, bottom: 0, width: 2, background: 'var(--border)' }} />

        {steps.map((step, idx) => (
          <div key={idx} style={{ position: 'relative', marginBottom: idx < steps.length - 1 ? 4 : 0 }}>
            {/* Node dot */}
            <div style={{
              position: 'absolute', left: -17, top: 10, width: 10, height: 10,
              borderRadius: '50%', background: stepColor(step.action_type),
              border: '2px solid var(--surface)', boxShadow: `0 0 0 2px ${stepColor(step.action_type)}33`,
              zIndex: 1
            }} />

            <div style={{
              marginLeft: 8, padding: '8px 12px', borderRadius: 'var(--radius)',
              background: stepBg(step.action_type), border: `1px solid ${stepColor(step.action_type)}22`,
              cursor: 'pointer', transition: 'all 0.15s'
            }} onClick={() => toggleStep(idx)}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: '0.8rem' }}>{stepIcon(step.action_type)}</span>
                  <span style={{ fontWeight: 600, fontSize: '0.8rem', color: stepColor(step.action_type) }}>
                    {step.action_type}
                  </span>
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>Step {step.step_number}</span>
                </div>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', transition: 'transform 0.15s', display: 'inline-block', transform: expandedSteps[idx] ? 'rotate(90deg)' : 'rotate(0deg)' }}>
                  &#9654;
                </span>
              </div>

              {!expandedSteps[idx] && (
                <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: 4, lineHeight: 1.4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 500 }}>
                  {step.thought}
                </div>
              )}

              {expandedSteps[idx] && (
                <div style={{ marginTop: 8 }}>
                  <div style={{ fontSize: '0.82rem', color: 'var(--text)', lineHeight: 1.5, marginBottom: 6 }}>
                    {step.thought}
                  </div>

                  {step.tool_name && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 8px', background: 'var(--blue-bg)', borderRadius: 4, fontSize: '0.78rem', marginBottom: 4 }}>
                      <span style={{ fontWeight: 600, color: 'var(--blue)' }}>Tool:</span>
                      <span style={{ fontFamily: 'monospace', color: 'var(--blue)' }}>{step.tool_name}</span>
                      {step.tool_args && Object.keys(step.tool_args).length > 0 && (
                        <span style={{ color: 'var(--text-muted)', fontFamily: 'monospace', fontSize: '0.72rem' }}>
                          {JSON.stringify(step.tool_args)}
                        </span>
                      )}
                    </div>
                  )}

                  {step.tool_result && (
                    <div style={{ padding: '4px 8px', background: 'var(--green-bg)', borderRadius: 4, fontSize: '0.78rem', color: 'var(--green)', fontFamily: 'monospace', marginTop: 4, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                      <span style={{ fontWeight: 600 }}>Result: </span>{step.tool_result.substring(0, 300)}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Summary footer */}
      <div style={{ marginTop: 12, padding: '8px 12px', background: 'var(--gray-bg)', borderRadius: 'var(--radius)', display: 'flex', gap: 16, fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
        <span>&#128257; Iterations: <strong style={{ color: 'var(--blue)' }}>{agentResponse.trace.iteration_count}</strong></span>
        <span>&#128295; Tool Calls: <strong style={{ color: 'var(--blue)' }}>{agentResponse.tool_calls_made}</strong></span>
        {agentResponse.trace.self_corrections > 0 && (
          <span>&#9888;&#65039; Self-Corrections: <strong style={{ color: 'var(--orange)' }}>{agentResponse.trace.self_corrections}</strong></span>
        )}
      </div>
    </div>
  );
}


export default AgentReasoningTree;
