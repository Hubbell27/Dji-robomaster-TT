import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { IntakeSummary } from "../lib/types";
import { useStaff } from "./StaffApp";

type View = "pending" | "completed" | "attention" | "all";

const VIEWS: { key: View; label: string }[] = [
  { key: "pending", label: "Pending" },
  { key: "completed", label: "Completed" },
  { key: "attention", label: "Expired / locked" },
  { key: "all", label: "All" },
];

export const STATUS_LABEL: Record<IntakeSummary["status"], string> = {
  pending: "Link sent",
  in_progress: "In progress",
  submitted: "Completed",
  expired: "Expired",
  locked: "Locked",
  cancelled: "Cancelled",
};

export function fmtDate(iso: string | null) {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—";
}

export function fmtDob(dob: string) {
  const [y, m, d] = dob.split("-");
  return `${m}/${d}/${y}`;
}

export function Dashboard() {
  const { call, me } = useStaff();
  const [view, setView] = useState<View>("pending");
  const [locationId, setLocationId] = useState("");
  const [locations, setLocations] = useState<{ id: string; name: string }[]>([]);
  const [q, setQ] = useState("");
  const [data, setData] = useState<{ total: number; items: IntakeSummary[] } | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [page, setPage] = useState(0);
  const pageSize = 50;

  useEffect(() => { call<typeof locations>("/api/staff/locations").then(setLocations); }, [call]);

  const load = useCallback(() => {
    const params = new URLSearchParams({ view, limit: String(pageSize), offset: String(page * pageSize) });
    if (locationId) params.set("location_id", locationId);
    if (q.trim()) params.set("q", q.trim());
    call<{ total: number; items: IntakeSummary[] }>(`/api/staff/intakes?${params}`).then(setData);
  }, [call, view, locationId, q, page]);

  useEffect(() => {
    const timer = window.setTimeout(load, q ? 300 : 0);
    return () => window.clearTimeout(timer);
  }, [load, q]);

  return (
    <>
      <div className="page-head">
        <h1>Patient intake forms</h1>
        <button className="primary" onClick={() => setShowNew(true)}>+ New intake link</button>
      </div>

      {showNew && <NewIntake locations={locations} defaultLocation={locationId || me.locations[0]?.id || locations[0]?.id}
        onClose={() => { setShowNew(false); load(); }} />}

      <div className="toolbar">
        <div className="tabs" role="tablist">
          {VIEWS.map((v) => (
            <button key={v.key} role="tab" aria-selected={view === v.key} className={view === v.key ? "on" : ""}
              onClick={() => { setView(v.key); setPage(0); }}>{v.label}</button>
          ))}
        </div>
        <div className="spacer" />
        {locations.length > 1 && (
          <select value={locationId} onChange={(e) => { setLocationId(e.target.value); setPage(0); }} aria-label="Location">
            <option value="">All my locations</option>
            {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        )}
        <input type="search" placeholder="Search name or DOB (YYYY-MM-DD)" value={q}
          onChange={(e) => { setQ(e.target.value); setPage(0); }} aria-label="Search" />
      </div>

      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Patient</th><th>DOB</th><th>Status</th><th>Location</th>
              <th>Created</th><th>{view === "completed" ? "Submitted" : "Link expires"}</th><th />
            </tr>
          </thead>
          <tbody>
            {data?.items.map((i) => (
              <tr key={i.id}>
                <td><Link to={`/staff/intakes/${i.id}`}>{i.patient.last_name}, {i.patient.first_name}</Link></td>
                <td>{fmtDob(i.patient.dob)}</td>
                <td><span className={`badge ${i.status}`}>{STATUS_LABEL[i.status]}</span></td>
                <td>{i.location.name}</td>
                <td>{fmtDate(i.created_at)}<div className="muted small">by {i.created_by}</div></td>
                <td>{view === "completed" ? fmtDate(i.submitted_at) : fmtDate(i.expires_at)}</td>
                <td><Link to={`/staff/intakes/${i.id}`}>Open →</Link></td>
              </tr>
            ))}
            {data && data.items.length === 0 && (
              <tr><td colSpan={7} className="muted center">No intake forms here.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {data && data.total > pageSize && (
        <div className="pager">
          <button className="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</button>
          <span className="muted">{page * pageSize + 1}–{Math.min((page + 1) * pageSize, data.total)} of {data.total}</span>
          <button className="secondary" disabled={(page + 1) * pageSize >= data.total} onClick={() => setPage(page + 1)}>Next</button>
        </div>
      )}
    </>
  );
}

export function LinkPanel({ link, onDone }: { link: string; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(link);
    setCopied(true);
  };
  return (
    <div className="link-panel">
      <p><strong>Patient link created.</strong> Copy it now and send it to the patient by text or email. For security it is shown only once. If it is lost, reissue a new link. It expires in 7 days.</p>
      <div className="link-box">
        <input readOnly value={link} onFocus={(e) => e.target.select()} aria-label="Patient link" />
        <button className="primary" onClick={copy}>{copied ? "Copied ✓" : "Copy link"}</button>
      </div>
      <p className="muted small">Do not include health details in the message. The patient will confirm their date of birth to open the form.</p>
      <button className="secondary" onClick={onDone}>Done</button>
    </div>
  );
}

function NewIntake({ locations, defaultLocation, onClose }: {
  locations: { id: string; name: string }[]; defaultLocation?: string; onClose: () => void;
}) {
  const { call } = useStaff();
  const [form, setForm] = useState({ first_name: "", last_name: "", dob: "", language: "en", location_id: defaultLocation ?? "" });
  const [link, setLink] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await call<{ link: string }>("/api/staff/intakes", { method: "POST", body: form });
      setLink(r.link);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="New intake link">
      <div className="modal">
        <h2>New patient intake link</h2>
        {link ? (
          <LinkPanel link={link} onDone={onClose} />
        ) : (
          <form onSubmit={submit}>
            <div className="grid-2">
              <label className="field"><span className="field-label">First name</span>
                <input required maxLength={100} value={form.first_name} onChange={(e) => setForm({ ...form, first_name: e.target.value })} /></label>
              <label className="field"><span className="field-label">Last name</span>
                <input required maxLength={100} value={form.last_name} onChange={(e) => setForm({ ...form, last_name: e.target.value })} /></label>
              <label className="field"><span className="field-label">Date of birth</span>
                <input required type="date" value={form.dob} onChange={(e) => setForm({ ...form, dob: e.target.value })} /></label>
              <label className="field"><span className="field-label">Form language</span>
                <select value={form.language} onChange={(e) => setForm({ ...form, language: e.target.value })}>
                  <option value="en">English</option><option value="es">Spanish / Español</option>
                </select></label>
              <label className="field"><span className="field-label">Location</span>
                <select required value={form.location_id} onChange={(e) => setForm({ ...form, location_id: e.target.value })}>
                  <option value="" disabled>Select…</option>
                  {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                </select></label>
            </div>
            <p className="muted small">The patient must enter this date of birth to unlock the form. Five wrong attempts lock the link.</p>
            {error && <p className="error">{error}</p>}
            <div className="nav-row">
              <button type="button" className="secondary" onClick={onClose}>Cancel</button>
              <button type="submit" className="primary" disabled={busy}>Create link</button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
