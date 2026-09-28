"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type Merchant = { id: string; name: string; policy_version: number | null };
type CaseItem = {
  id: string; merchant_id: string; transaction_id: string; state: string; created_at: string;
  amount: number; currency: string; transaction_status: string; merchant_descriptor: string;
  customer_ref: string; risk_score: number; risk_level: string; intervention_opportunity: string;
  signals: { name?: string; description?: string }[];
};
type Incident = { id: string; incident_type: string; status: string; current_disputes: number;
  current_transactions: number; current_dispute_rate: number | string | null; baseline_dispute_rate: number | string | null;
  rate_multiplier: number | string | null; window_started_at: string; created_at: string };
type Outcome = { id: string; outcome_status: string; dispute_occurred: boolean | null;
  complaint_after_intervention: boolean | null; resolution_status: string | null; window_ends_at: string };
type Panel = "cases" | "incidents" | "outcomes";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/backend${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers }, cache: "no-store" });
  } catch (err) {
    throw new Error("Could not connect to DisputeShield API server. Please ensure backend is running at http://localhost:8000.");
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = typeof data.detail === "string" ? data.detail : data.detail?.message;
    const counts = data.detail?.record_counts;
    const suffix = counts ? ` Protected data: ${Object.entries(counts).map(([key, count]) => `${key} ${count}`).join(", ")}.` : "";
    throw new Error(`${detail || `Request failed (${response.status})`}${suffix}`);
  }
  return data as T;
}
const money = (amount: number, currency: string) => new Intl.NumberFormat(undefined, { style: "currency", currency }).format(amount / 100);
const date = (value: string) => new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
function Empty({ text }: { text: string }) { return <div className="empty">{text}</div>; }

export default function Home() {
  const [merchants, setMerchants] = useState<Merchant[]>([]);
  const [merchantId, setMerchantId] = useState("");
  const [newMerchant, setNewMerchant] = useState("");
  const [cases, setCases] = useState<CaseItem[]>([]);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [outcomes, setOutcomes] = useState<Outcome[]>([]);
  const [panel, setPanel] = useState<Panel>("cases");
  const [selectedCase, setSelectedCase] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [showDeleteModal, setShowDeleteModal] = useState(false);
  const [deleteText, setDeleteText] = useState("");
  const [deleteError, setDeleteError] = useState("");

  const loadMerchants = useCallback(async () => {
    try {
      const result = await request<{ items: Merchant[] }>("/api/merchants");
      setMerchants(result.items);
      const saved = localStorage.getItem("ds-merchant");
      const chosen = result.items.some((item) => item.id === saved) ? saved! : result.items[0]?.id || "";
      setMerchantId((old) => result.items.some((item) => item.id === old) ? old : chosen);
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load merchants"); }
  }, []);

  const loadWorkspace = useCallback(async (id: string) => {
    if (!id) { setCases([]); setIncidents([]); return; }
    setLoading(true); setError(""); localStorage.setItem("ds-merchant", id);
    try {
      const [caseResult, incidentResult] = await Promise.all([
        request<{ items: CaseItem[] }>(`/api/cases?merchant_id=${encodeURIComponent(id)}&limit=100`),
        request<{ items: Incident[] }>(`/api/incidents?merchant_id=${encodeURIComponent(id)}&limit=100`),
      ]);
      setCases(caseResult.items); setIncidents(incidentResult.items);
      setSelectedCase((old) => caseResult.items.some((item) => item.id === old) ? old : caseResult.items[0]?.id || "");
    } catch (e) { setError(e instanceof Error ? e.message : "Could not load workspace"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { void loadMerchants(); }, [loadMerchants]);
  useEffect(() => { if (merchantId) void loadWorkspace(merchantId); else setLoading(false); }, [merchantId, loadWorkspace]);
  useEffect(() => {
    if (!merchantId || !selectedCase) { setOutcomes([]); return; }
    request<{ items: Outcome[] }>(`/api/cases/${selectedCase}/outcomes?merchant_id=${encodeURIComponent(merchantId)}`)
      .then((result) => setOutcomes(result.items)).catch(() => setOutcomes([]));
  }, [merchantId, selectedCase]);

  const merchant = merchants.find((item) => item.id === merchantId);
  const visibleCases = useMemo(() => cases.filter((item) =>
    `${item.customer_ref} ${item.merchant_descriptor} ${item.state} ${item.risk_level} ${item.transaction_id}`.toLowerCase().includes(query.toLowerCase())), [cases, query]);
  const openIncidents = incidents.filter((item) => item.status !== "RESOLVED").length;
  const highRisk = cases.filter((item) => ["HIGH", "CRITICAL"].includes(item.risk_level)).length;
  const refresh = async () => { if (merchantId) await loadWorkspace(merchantId); };
  const createMerchant = async (event: React.FormEvent) => {
    event.preventDefault(); setWorking(true); setError("");
    try {
      const created = await request<Merchant>("/api/merchants", { method: "POST", body: JSON.stringify({ name: newMerchant }) });
      setNewMerchant(""); await loadMerchants(); setMerchantId(created.id); setNotice("Merchant workspace created with the default policy.");
    } catch (e) { setError(e instanceof Error ? e.message : "Merchant setup failed"); }
    finally { setWorking(false); }
  };
  const deleteMerchant = async () => {
    if (!merchant) return;
    if (deleteText !== merchant.name) return;
    setWorking(true); setError(""); setNotice("");
    try {
      await request(`/api/merchants/${merchant.id}`, { method: "DELETE", body: JSON.stringify({ confirmation: deleteText }) });
      setShowDeleteModal(false); setDeleteText("");
      setNotice(`Workspace “${merchant.name}” was deleted.`);
      setMerchantId("");
      await loadMerchants();
    } catch (e) { setDeleteError(e instanceof Error ? e.message : "Workspace deletion failed"); }
    finally { setWorking(false); }
  };
  const detectIncident = async () => {
    if (!merchantId) return;
    setWorking(true); setError(""); setNotice("");
    try {
      const result = await request<{ triggered: boolean; conflict?: boolean; reason?: string }>("/api/incidents/detect", {
        method: "POST", body: JSON.stringify({ merchant_id: merchantId }),
      });
      setNotice(result.triggered ? (result.conflict ? "Spike matched an existing open incident." : "A dispute rate spike incident was opened.") : "No incident threshold was met for this window.");
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "Incident check failed"); }
    finally { setWorking(false); }
  };
  const changeIncident = async (incident: Incident, status: "ACKNOWLEDGED" | "RESOLVED") => {
    if (!merchantId) return;
    setWorking(true); setError("");
    try {
      await request(`/api/incidents/${incident.id}/status`, { method: "POST", body: JSON.stringify({
        merchant_id: merchantId, status, actor_id: "local-demo-user", resolution_note: status === "RESOLVED" ? "Resolved in dashboard" : null,
      }) });
      await refresh();
    } catch (e) { setError(e instanceof Error ? e.message : "Incident update failed"); }
    finally { setWorking(false); }
  };
  const refreshOutcomes = async () => {
    if (!merchantId || !selectedCase) return;
    setWorking(true); setError("");
    try {
      await request(`/api/cases/${selectedCase}/outcomes/refresh?merchant_id=${encodeURIComponent(merchantId)}`, { method: "POST" });
      const result = await request<{ items: Outcome[] }>(`/api/cases/${selectedCase}/outcomes?merchant_id=${encodeURIComponent(merchantId)}`);
      setOutcomes(result.items); setNotice("Outcome observations refreshed from persisted events and disputes.");
    } catch (e) { setError(e instanceof Error ? e.message : "Outcome refresh failed"); }
    finally { setWorking(false); }
  };

  return <div className="shell">
    <aside className="sidebar">
      <div className="brand"><span className="brand-mark">◈</span><span>disputeshield<small>PREVENTION CONSOLE</small></span></div>
      <label className="field-label" htmlFor="merchant">MERCHANT WORKSPACE</label>
      {merchants.length ? <select id="merchant" value={merchantId} onChange={(e) => setMerchantId(e.target.value)}>
        {merchants.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
      </select> : <div className="empty-merchant">No merchant workspace yet.</div>}
      {merchant && <button className="delete-workspace" disabled={working} onClick={() => { setDeleteText(""); setDeleteError(""); setShowDeleteModal(true); }}>Delete selected workspace</button>}
      <div className="nav-label">WORKSPACE</div>
      <nav>{([ ["cases", "◈", "Risk queue"], ["incidents", "⌁", "Incidents"], ["outcomes", "◷", "Outcomes"] ] as [Panel,string,string][]).map(([key, icon, label]) =>
        <button key={key} className={panel === key ? "nav-item active" : "nav-item"} onClick={() => setPanel(key)}><span>{icon}</span>{label}<b>{key === "cases" ? cases.length : key === "incidents" ? openIncidents : outcomes.length}</b></button>)}
      </nav>
      <div className="sidebar-bottom"><span className="online-dot"/>Local demo workspace<small>Actions are simulated only</small></div>
    </aside>
    <main className="main">
      <header className="topbar"><div><span className="muted">{merchant?.name || "Workspace setup"}</span><span className="slash">/</span><b>{panel === "cases" ? "Risk queue" : panel === "incidents" ? "Merchant incidents" : "Outcome monitoring"}</b></div><div className="top-actions"><span className="api-state"><i/> API connected</span><button className="button secondary" onClick={() => void refresh()} disabled={loading || working}>Refresh data</button></div></header>
      <section className="welcome"><div><div className="eyebrow">POST-PAYMENT PROTECTION</div><h1>{panel === "cases" ? "Your risk queue" : panel === "incidents" ? "Merchant incidents" : "Outcome monitoring"}</h1><p>Review persisted merchant data and tracked post-payment outcomes.</p></div>
        {panel === "incidents" && merchantId && <button className="button primary" onClick={() => void detectIncident()} disabled={working}>Check dispute spike <span>↗</span></button>}
        {panel === "outcomes" && selectedCase && <button className="button primary" onClick={() => void refreshOutcomes()} disabled={working}>Refresh observations <span>↻</span></button>}
      </section>
      {error && <div className="alert error">{error}<button onClick={() => setError("")}>×</button></div>}
      {notice && <div className="alert success">{notice}<button onClick={() => setNotice("")}>×</button></div>}
      {!merchants.length && !loading ? <section className="setup-card"><div className="setup-icon">＋</div><h2>Create your first merchant workspace</h2><p>Start with a local demo merchant and its default policy. The dashboard becomes populated when transactions and assessments are added through the project’s API or data tooling.</p><form onSubmit={createMerchant}><input value={newMerchant} onChange={(e) => setNewMerchant(e.target.value)} placeholder="Merchant name" minLength={2} maxLength={120} required/><button className="button primary" disabled={working || !newMerchant.trim()}>Create workspace</button></form><small>Merchant IDs are local demo scopes, not user authentication.</small></section> : <>
      <section className="stat-grid">
        <article className="stat-card"><span>Open cases</span><b>{loading ? "—" : cases.length}</b><small>persisted case records</small></article>
        <article className="stat-card"><span>Elevated risk</span><b>{loading ? "—" : highRisk}</b><small>high or critical assessments</small></article>
        <article className="stat-card"><span>Active incidents</span><b>{loading ? "—" : openIncidents}</b><small>merchant scoped</small></article>
        <article className="stat-card"><span>Observation windows</span><b>{loading ? "—" : outcomes.length}</b><small>{outcomes.filter((item) => item.outcome_status === "OBSERVING").length} currently observing</small></article>
      </section>
      {panel === "cases" && <section className="panel">
        <div className="panel-head"><div><h2>Cases to review</h2><p>Risk scores and states from the API</p></div><input className="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search cases"/></div>
        {!merchantId ? <Empty text="Select or create a merchant workspace to view cases."/> : loading ? <Empty text="Loading merchant cases…"/> : !visibleCases.length ? <Empty text="No cases found for this merchant. Cases appear after deterministic risk assessment meets the opening threshold."/> : <div className="table-wrap"><table><thead><tr><th>CASE / TRANSACTION</th><th>DESCRIPTOR</th><th>RISK</th><th>OPPORTUNITY</th><th>STATE</th><th>AMOUNT</th></tr></thead><tbody>{visibleCases.map((item) => <tr key={item.id} className={selectedCase === item.id ? "selected" : ""} onClick={() => setSelectedCase(item.id)}>
          <td><b className="mono">{item.id.slice(0, 8)}</b><small>{item.customer_ref || item.transaction_id.slice(0, 8)}</small></td><td>{item.merchant_descriptor}<small>{item.signals?.[0]?.description || item.signals?.[0]?.name || "Persisted risk signals"}</small></td><td><span className={`pill risk-${item.risk_level.toLowerCase()}`}>{item.risk_level}</span></td><td>{item.intervention_opportunity}</td><td><span className="pill neutral">{item.state.replaceAll("_", " ")}</span></td><td>{money(item.amount, item.currency)}</td>
        </tr>)}</tbody></table></div>}
      </section>}
      {panel === "incidents" && <section className="panel"><div className="panel-head"><div><h2>Dispute rate incidents</h2><p>Measured against each merchant’s trailing baseline and configured thresholds</p></div></div>
        {!incidents.length ? <Empty text={loading ? "Loading merchant incidents…" : "No incidents recorded. Run a dispute spike check to evaluate persisted counts."}/> : <div className="table-wrap"><table><thead><tr><th>INCIDENT</th><th>STATUS</th><th>CURRENT</th><th>BASELINE</th><th>RATE CHANGE</th><th>DETECTED</th><th>ACTION</th></tr></thead><tbody>{incidents.map((item) => <tr key={item.id}><td><b>Dispute rate spike</b><small className="mono">{item.id.slice(0, 8)}</small></td><td><span className={`pill ${item.status === "RESOLVED" ? "neutral" : "risk-high"}`}>{item.status}</span></td><td>{item.current_disputes} / {item.current_transactions}<small>disputes / payments</small></td><td>{item.baseline_dispute_rate == null ? "No baseline" : `${(Number(item.baseline_dispute_rate) * 100).toFixed(2)}%`}</td><td>{item.rate_multiplier == null ? "New activity" : `${Number(item.rate_multiplier).toFixed(2)}×`}</td><td>{date(item.created_at)}</td><td className="actions">{item.status === "OPEN" && <button className="text-button" disabled={working} onClick={() => void changeIncident(item, "ACKNOWLEDGED")}>Acknowledge</button>}{item.status !== "RESOLVED" && <button className="text-button" disabled={working} onClick={() => void changeIncident(item, "RESOLVED")}>Resolve</button>}</td></tr>)}</tbody></table></div>}
      </section>}
      {panel === "outcomes" && <section className="panel"><div className="panel-head"><div><h2>Intervention observations</h2><p>Absence of a dispute is only recorded after the observation window completes.</p></div><select className="case-select" value={selectedCase} onChange={(e) => setSelectedCase(e.target.value)}><option value="">Select a case</option>{cases.map((item) => <option key={item.id} value={item.id}>{item.merchant_descriptor} · {item.id.slice(0, 8)}</option>)}</select></div>
        {!selectedCase ? <Empty text="Select a case with a simulated intervention to see its outcome window."/> : outcomes.length === 0 ? <Empty text="No outcome window is recorded for this case yet. A window starts when an intervention is simulated."/> : <div className="outcome-list">{outcomes.map((item) => <article className="outcome" key={item.id}><div className="outcome-top"><div><span className="eyebrow">OBSERVATION WINDOW</span><h3>{item.outcome_status}</h3></div><span className={`pill ${item.outcome_status === "OBSERVING" ? "risk-medium" : "neutral"}`}>{item.outcome_status}</span></div><p>Window ends {date(item.window_ends_at)}</p><div className="outcome-facts"><div><small>Dispute observed</small><b>{item.dispute_occurred === null ? "Not known yet" : item.dispute_occurred ? "Yes" : "No dispute observed"}</b></div><div><small>Complaint observed</small><b>{item.complaint_after_intervention === null ? "Not known yet" : item.complaint_after_intervention ? "Yes" : "No complaint observed"}</b></div><div><small>Resolution record</small><b>{item.resolution_status || "No resolution recorded yet"}</b></div></div></article>)}</div>}
      </section>}
      </>}
      <footer><span>DisputeShield · local demo</span><span>Simulated actions do not contact customers or move money.</span></footer>
    </main>
    {showDeleteModal && merchant && <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setShowDeleteModal(false); }}><section className="delete-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-title"><button className="dialog-close" aria-label="Close" onClick={() => setShowDeleteModal(false)}>×</button><span className="eyebrow">REMOVE WORKSPACE</span><h2 id="delete-title">Delete {merchant.name}?</h2><p>This permanently removes the empty workspace and its default policy. Workspaces with customer, transaction, event, dispute, case, or incident data cannot be deleted here.</p><label htmlFor="confirm-merchant">Type <b>{merchant.name}</b> to confirm</label><input id="confirm-merchant" autoFocus value={deleteText} onChange={(event) => setDeleteText(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && deleteText === merchant.name) void deleteMerchant(); }} />{deleteError && <div className="dialog-error">{deleteError}</div>}<div className="dialog-actions"><button className="button secondary" onClick={() => setShowDeleteModal(false)}>Cancel</button><button className="button danger" disabled={working || deleteText !== merchant.name} onClick={() => void deleteMerchant()}>{working ? "Deleting…" : "Delete workspace"}</button></div></section></div>}
  </div>;
}
