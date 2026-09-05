import React from 'react';
function UploadPanel({ onUploadComplete, loading }) {
  const [files, setFiles] = React.useState({ transactions: null, settlements: null, refunds: null, bank_credits: null });
  const [uploading, setUploading] = React.useState(false);
  const [error, setError] = React.useState("");
  const [dragOver, setDragOver] = React.useState(false);
  const [fetchingRazorpay, setFetchingRazorpay] = React.useState(false);
  const [razorpayError, setRazorpayError] = React.useState("");
  const [razorpayDays, setRazorpayDays] = React.useState("7");
  const inputRefs = React.useRef({});

  const fileLabels = {
    transactions: "Transactions",
    settlements: "Settlements",
    refunds: "Refunds",
    bank_credits: "Bank Credits"
  };

  const handleFile = (key, e) => {
    const file = e.target.files[0];
    if (file && !file.name.endsWith('.csv')) {
      setError(`${file.name} is not a CSV file`);
      return;
    }
    setError("");
    setFiles(prev => ({ ...prev, [key]: file }));
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    const dropped = Array.from(e.dataTransfer.files).filter(f => f.name.endsWith('.csv'));
    if (dropped.length === 0) {
      setError("No CSV files found in drop");
      return;
    }
    setError("");
    setFiles(prev => {
      const next = { ...prev };
      const keys = Object.keys(next);
      let di = 0;
      for (const key of keys) {
        if (!next[key] && di < dropped.length) {
          next[key] = dropped[di++];
        }
      }
      return next;
    });
  };

  const allSelected = Object.values(files).every(f => f !== null);
  const selectedCount = Object.values(files).filter(f => f !== null).length;

  const upload = async () => {
    if (!allSelected) {
      setError("Please select all 4 CSV files");
      return;
    }
    setError("");
    setUploading(true);
    const form = new FormData();
    form.append("transactions", files.transactions);
    form.append("settlements", files.settlements);
    form.append("refunds", files.refunds);
    form.append("bank_credits", files.bank_credits);
    try {
      const resp = await fetch("/upload", { method: "POST", body: form });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      if (data.status === "error") {
        setError("Server error during processing. Check your CSV files.");
        setUploading(false);
        return;
      }
      onUploadComplete(data.job_id, data.upload_hash);
    } catch (err) {
      setError(`Upload failed: ${err.message}`);
    }
    setUploading(false);
  };

  const clearFiles = () => {
    setFiles({ transactions: null, settlements: null, refunds: null, bank_credits: null });
    setError("");
    Object.values(inputRefs.current).forEach(el => { if (el) el.value = ""; });
  };

  const fetchFromRazorpay = async () => {
    setRazorpayError("");
    setFetchingRazorpay(true);
    try {
      const resp = await fetch("/api/reconcile-razorpay", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ count: 100, days: Number(razorpayDays) || 7 }),
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${resp.status}`);
      }
      const data = await resp.json();
      if (data.status === "empty") {
        setRazorpayError("No settlements found for the selected date range.");
        setFetchingRazorpay(false);
        return;
      }
      onUploadComplete(data.job_id);
    } catch (err) {
      setRazorpayError(`Razorpay fetch failed: ${err.message}`);
    }
    setFetchingRazorpay(false);
  };

  return (
    <div className="card">
      <div className="card-header">
        <div>
          <div className="card-title">Upload CSV Files</div>
          <div className="card-subtitle">Provide 4 CSV files to run reconciliation</div>
        </div>
        {selectedCount > 0 && (
          <button className="btn btn-sm" onClick={clearFiles}>Clear All</button>
        )}
      </div>

      <div
        className={`upload-area ${dragOver ? "dragover" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
      >
        <div className="upload-icon">📁</div>
        <div className="upload-title">Drop CSV files here</div>
        <div className="upload-hint">or click the slots below to browse</div>

        <div className="file-slots">
          {Object.entries(fileLabels).map(([key, label]) => (
            <div key={key}>
              <input
                type="file"
                accept=".csv"
                ref={el => inputRefs.current[key] = el}
                id={`file-${key}`}
                onChange={e => handleFile(key, e)}
              />
              <label className={`file-slot ${files[key] ? "filled" : ""}`} htmlFor={`file-${key}`}>
                <div className="slot-label">{label}</div>
                {files[key]
                  ? <div className="slot-file">{files[key].name}</div>
                  : <div className="slot-empty">Select CSV</div>
                }
              </label>
            </div>
          ))}
        </div>
      </div>

      {error && <div className="upload-error">{error}</div>}

      <div className="upload-actions">
        <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
          {selectedCount}/4 files selected
        </div>
        <button
          className="btn btn-primary"
          disabled={!allSelected || uploading || loading}
          onClick={upload}
        >
          {(uploading || loading) && <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} />}
          {uploading ? "Processing..." : "Upload & Reconcile"}
        </button>
      </div>

      <div style={{ borderTop: '1px solid var(--border)', margin: '16px 0 0', paddingTop: '16px' }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
          <div>
            <div style={{ fontWeight: 600, fontSize: '0.9rem' }}>Import from Razorpay</div>
            <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
              Import settlements for review. Upload CSVs for full reconciliation.
            </div>
          </div>
          <select value={razorpayDays} onChange={e => setRazorpayDays(e.target.value)} aria-label="Razorpay date range">
            <option value="1">Last 24 hours</option>
            <option value="7">Last 7 days</option>
            <option value="30">Last 30 days</option>
            <option value="custom">Custom range</option>
          </select>
          <button
            className="btn"
            disabled={fetchingRazorpay || uploading || loading}
            onClick={fetchFromRazorpay}
            style={{ background: '#6366f1', color: '#fff', whiteSpace: 'nowrap' }}
          >
            {fetchingRazorpay && <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} />}
            {fetchingRazorpay ? "Importing..." : "Import settlements"}
          </button>
        </div>
        {razorpayError && <div className="upload-error" style={{ marginTop: '8px' }}>{razorpayError}</div>}
      </div>
    </div>
  );
}


export default UploadPanel;
