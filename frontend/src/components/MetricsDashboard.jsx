import React, { useEffect, useState } from "react";
import {
  PieChart,
  Pie,
  Cell,
  ResponsiveContainer,
  Tooltip,
  Legend,
} from "recharts";

const API = "/api";

const DECISION_COLORS = {
  clean: "#16a34a",
  exceptions: "#f59e0b",
  math_discrepancy: "#3b82f6",
  unresolved: "#dc2626",
};

const DECISION_LABELS = {
  clean: "Clean Match",
  exceptions: "Exceptions",
  math_discrepancy: "Math Discrepancy",
  unresolved: "Unresolved",
};

function StatCard({ label, value, sub, accent }) {
  return (
    <div className="card" style={{ padding: "14px 18px", minWidth: 0 }}>
      <div style={{ fontSize: "0.72rem", letterSpacing: "0.08em", textTransform: "uppercase", color: "#94a3b8" }}>
        {label}
      </div>
      <div style={{ fontSize: "1.35rem", fontWeight: 700, color: accent || "#e2e8f0", marginTop: 4 }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: "0.78rem", color: "#64748b", marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

function GroqQuotaBar({ quota, activeAi }) {
  if (!quota) return null;
  const pct = Math.min(100, quota.pct_used);
  return (
    <div className="card" style={{ padding: "18px 20px" }}>
      <div className="flex-between" style={{ marginBottom: 10 }}>
        <span style={{ fontWeight: 600 }}>Groq Free-Tier Quota</span>
        <span style={{ fontSize: "0.78rem", color: activeAi ? "#16a34a" : "#dc2626" }}>
          {activeAi ? "AI Active" : "AI Unavailable (set GROQ_API_KEY)"}
        </span>
      </div>
      <div style={{ display: "flex", gap: 16, alignItems: "baseline" }}>
        <span style={{ fontSize: "1.1rem", fontWeight: 700 }}>
          {quota.used_tokens.toLocaleString("en-IN")}
        </span>
        <span style={{ fontSize: "0.8rem", color: "#94a3b8" }}>
          / {quota.daily_limit.toLocaleString("en-IN")} tokens today
        </span>
        <span style={{ fontSize: "0.8rem", color: "#60a5fa", marginLeft: "auto" }}>
          {quota.remaining_tokens.toLocaleString("en-IN")} remaining
        </span>
      </div>
      <div style={{ height: 10, borderRadius: 6, background: "#1e293b", marginTop: 10, overflow: "hidden" }}>
        <div
          style={{
            height: "100%",
            width: `${pct}%`,
            borderRadius: 6,
            background: pct >= 90 ? "#dc2626" : pct >= 60 ? "#f59e0b" : "#22c55e",
            transition: "width .4s ease",
          }}
        />
      </div>
      <div style={{ fontSize: "0.75rem", color: "#64748b", marginTop: 8 }}>
        llama-3.1-70b &middot; {Object.entries(quota.by_model || {}).length || 0} model(s) reporting
      </div>
    </div>
  );
}

function DecisionPie({ breakdown }) {
  const data = Object.entries(breakdown || {}).map(([key, value]) => ({
    name: DECISION_LABELS[key] || key,
    value,
    color: DECISION_COLORS[key] || "#94a3b8",
  }));
  const total = data.reduce((acc, d) => acc + d.value, 0);

  return (
    <div className="card" style={{ padding: "18px 20px" }}>
      <div style={{ fontWeight: 600, marginBottom: 12 }}>Decisions</div>
      {total === 0 ? (
        <div className="empty-state" style={{ padding: "26px 0" }}>
          <div style={{ color: "#64748b", fontSize: "0.9rem" }}>
            No processed batches yet. Upload a batch to populate this chart.
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <ResponsiveContainer width="55%" height={220}>
            <PieChart>
              <Pie
                data={data}
                dataKey="value"
                nameKey="name"
                innerRadius={52}
                outerRadius={82}
                paddingAngle={2}
                stroke="#0b1120"
              >
                {data.map((entry) => (
                  <Cell key={entry.name} fill={entry.color} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{ background: "#0f172a", border: "1px solid #1e293b", borderRadius: 8 }}
                itemStyle={{ color: "#e2e8f0" }}
              />
              <Legend wrapperStyle={{ fontSize: "0.72rem", color: "#cbd5e1" }} />
            </PieChart>
          </ResponsiveContainer>
          <div style={{ width: "45%" }}>
            {data.map((d) => (
              <div key={d.name} className="flex-between" style={{ padding: "5px 0", fontSize: "0.82rem" }}>
                <span>
                  <span style={{ display: "inline-block", width: 9, height: 9, borderRadius: "50%", background: d.color, marginRight: 8 }} />
                  {d.name}
                </span>
                <span style={{ fontWeight: 600, color: "#e2e8f0" }}>{d.value}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function MetricsDashboard() {
  const [metrics, setMetrics] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const fetchMetrics = async () => {
      try {
        const resp = await fetch(`${API}/metrics`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        if (!cancelled) {
          setMetrics(data);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    };
    fetchMetrics();
    const interval = setInterval(fetchMetrics, 15000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (error && !metrics) {
    return (
      <div className="card">
        <div className="empty-state">
          <div className="empty-icon">📉</div>
          <div className="empty-title">Metrics unavailable</div>
          <div className="empty-hint">{error}</div>
        </div>
      </div>
    );
  }

  const llm = metrics?.llm || {};
  const quota = metrics?.groq_free_tier || null;

  return (
    <div>
      <div className="grid" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))", gap: 14, marginBottom: 16 }}>
        <StatCard label="Batches Processed" value={metrics ? metrics.batches_processed : "—"} />
        <StatCard label="Settlements Processed" value={metrics ? metrics.settlements_processed : "—"} />
        <StatCard
          label="Avg Match Rate"
          value={metrics ? `${(metrics.avg_match_rate * 100).toFixed(1)}%` : "—"}
          accent="#22c55e"
        />
        <StatCard
          label="AI Investigations"
          value={metrics ? metrics.ai_investigations_total : "—"}
          accent="#60a5fa"
        />
        <StatCard
          label="AI Auto-Approved"
          value={metrics ? metrics.ai_auto_approved_total : "—"}
          sub="schema-enforced = 0"
          accent={metrics && metrics.ai_auto_approved_total === 0 ? "#16a34a" : "#dc2626"}
        />
        <StatCard
          label="Error Rate"
          value={metrics ? `${(metrics.error_rate * 100).toFixed(2)}%` : "—"}
          accent={(metrics?.error_rate || 0) === 0 ? "#16a34a" : "#dc2626"}
        />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 16 }}>
        <DecisionPie breakdown={metrics?.decision_breakdown} />
        <GroqQuotaBar quota={quota} activeAi={!!metrics?.active_ai} />
      </div>

      <div className="card" style={{ padding: "18px 20px" }}>
        <div style={{ fontWeight: 600, marginBottom: 12 }}>LLM Telemetry</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: 12 }}>
          <div>
            <div style={{ fontSize: "0.72rem", color: "#94a3b8", textTransform: "uppercase" }}>Calls</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700 }}>{llm.total_calls ?? "—"}</div>
          </div>
          <div>
            <div style={{ fontSize: "0.72rem", color: "#94a3b8", textTransform: "uppercase" }}>Avg Latency</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700 }}>
              {llm.avg_latency_ms != null ? `${llm.avg_latency_ms.toFixed(0)} ms` : "—"}
            </div>
          </div>
          <div>
            <div style={{ fontSize: "0.72rem", color: "#94a3b8", textTransform: "uppercase" }}>Errors</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: (llm.errors || 0) > 0 ? "#dc2626" : "#16a34a" }}>
              {llm.errors ?? "—"}
            </div>
          </div>
          <div>
            <div style={{ fontSize: "0.72rem", color: "#94a3b8", textTransform: "uppercase" }}>LLM Error Rate</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700 }}>
              {llm.error_rate != null ? `${(llm.error_rate * 100).toFixed(2)}%` : "—"}
            </div>
          </div>
          <div>
            <div style={{ fontSize: "0.72rem", color: "#94a3b8", textTransform: "uppercase" }}>Est. Cost</div>
            <div style={{ fontSize: "1.1rem", fontWeight: 700, color: "#22c55e" }}>
              {metrics ? `\u20B9${(metrics.estimated_cost_inr || 0).toFixed(2)}` : "—"}
            </div>
            <div style={{ fontSize: "0.72rem", color: "#64748b" }}>Groq free tier</div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default MetricsDashboard;