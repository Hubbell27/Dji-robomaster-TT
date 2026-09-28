import QRCode from "qrcode";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import type { IntakeSummary } from "../lib/types";
import { useStaff } from "./StaffApp";

type View = "today" | "pending" | "review" | "completed" | "attention" | "all";

const VIEWS: { key: View; label: string; count?: "today" | "pending" | "review" | "attention" }[] = [
  { key: "today", label: "Today", count: "today" },
  { key: "pending", label: "Waiting on patient", count: "pending" },
  { key: "review", label: "Needs review", count: "review" },
  { key: "completed", label: "Completed" },
  { key: "attention", label: "Expired / locked", count: "attention" },
  { key: "all", label: "All" },
];

export const STATUS_LABEL: Record<IntakeSummary["status"], string> = {
  pending: "Link sent",
  in_progress: "In progress",
  submitted: "Completed",
  expired: "Expired",
  locked: "Locked",
  cancelled: "Cancelled",
  purged: "Purged",
};

// All dates are shown in the office's time zone (set at install), not the
// viewing device's, so "today" means the same thing everywhere.
let officeTz: string | undefined;
export function setOfficeTimezone(tz: string) {
  officeTz = tz;
}

export function fmtDate(iso: string | null) {
  return iso ? new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short", timeZone: officeTz }) : "—";
}

export function fmtTime(iso: string | null) {
  return iso ? new Date(iso).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", timeZone: officeTz }) : "—";
}

/** Today's date (YYYY-MM-DD) in the office time zone. */
export function officeToday() {
  return new Date().toLocaleDateString("en-CA", { timeZone: officeTz });
}

export function fmtDob(dob: string) {
  if (!dob) return "—";
  const [y, m, d] = dob.split("-");
  return `${m}/${d}/${y}`;
}

export function StatusBadge({ i }: { i: IntakeSummary }) {
  if (i.status === "submitted")
    return <span className={`badge ${i.reviewed_at ? "reviewed" : "submitted"}`}>{i.reviewed_at ? "Reviewed ✓" : "Needs review"}</span>;
  return <span className={`badge ${i.status}`}>{STATUS_LABEL[i.status]}</span>;
}

export function Alerts({ alerts, max = 3 }: { alerts: IntakeSummary["alerts"]; max?: number }) {
  if (!alerts.length) return null;
  const shown = alerts.slice(0, max);
  return (
    <div className="alerts">
      {shown.map((a) => <span key={a.code} className={`alert-chip ${a.level}`}>{a.label}</span>)}
      {alerts.length > max && <span className="alert-chip more">+{alerts.length - max} more</span>}
    </div>
  );
}

const REFRESH_MS = 30_000;

export function Dashboard() {
  const { call, me } = useStaff();
  const [view, setView] = useState<View>("today");
  const [locationId, setLocationId] = useState("");
  const [locations, setLocations] = useState<{ id: string; name: string }[]>([]);
  const [q, setQ] = useState("");
  const [data, setData] = useState<{ total: number; items: IntakeSummary[] } | null>(null);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [showNew, setShowNew] = useState(false);
  const [page, setPage] = useState(0);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const pageSize = 50;

  useEffect(() => { call<typeof locations>("/api/staff/locations").then(setLocations); }, [call]);

  const load = useCallback((auto = false) => {
    const params = new URLSearchParams({ view, limit: String(pageSize), offset: String(page * pageSize) });
    if (locationId) params.set("location_id", locationId);
    if (q.trim()) params.set("q", q.trim());
    if (auto) params.set("auto", "true");
    const cparams = locationId ? `?location_id=${locationId}` : "";
    return Promise.all([
      call<{ total: number; items: IntakeSummary[] }>(`/api/staff/intakes?${params}`).then(setData),
      call<Record<string, number>>(`/api/staff/intakes/counts${cparams}`).then(setCounts),
    ]).then(() => setUpdatedAt(new Date())).catch(() => undefined);
  }, [call, view, locationId, q, page]);

  useEffect(() => {
    const timer = window.setTimeout(() => load(), q ? 300 : 0);
    return () => window.clearTimeout(timer);
  }, [load, q]);

  // Keep the board current through the day; pause while the tab is hidden or a modal is open.
  useEffect(() => {
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && !showNew) load(true);
    }, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load, showNew]);

  // Keyboard: "n" opens a new intake from anywhere on the dashboard.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (e.key === "n" && !showNew && !["INPUT", "TEXTAREA", "SELECT"].includes(tag) && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        setShowNew(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showNew]);

  const isToday = view === "today";

  return (
    <>
      <div className="page-head">
        <div>
          <h1>{isToday ? `Today · ${new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", timeZone: officeTz })}` : "Patient intake forms"}</h1>
          <p className="muted small">
            {updatedAt ? `Updated ${updatedAt.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit", second: "2-digit", timeZone: officeTz })} · refreshes every 30 s` : "Loading…"}
          </p>
        </div>
        <button className="primary" onClick={() => setShowNew(true)} title="Shortcut: N">+ New patient intake</button>
      </div>

      <div className="stat-row">
        <button className="stat" onClick={() => setView("today")}><strong>{counts.today ?? "–"}</strong><span>on today's list</span></button>
        <button className="stat" onClick={() => setView("pending")}><strong>{counts.pending ?? "–"}</strong><span>waiting on patient</span></button>
        <button className={`stat ${counts.review ? "hot" : ""}`} onClick={() => setView("review")}><strong>{counts.review ?? "–"}</strong><span>completed, need review</span></button>
        <button className={`stat ${counts.attention ? "warn" : ""}`} onClick={() => setView("attention")}><strong>{counts.attention ?? "–"}</strong><span>expired or locked</span></button>
      </div>

      {showNew && <NewIntake locations={locations} defaultLocation={locationId || me.locations[0]?.id || locations[0]?.id}
        onClose={() => { setShowNew(false); load(); }} onCreated={() => load()} />}

      <div className="toolbar">
        <div className="tabs" role="tablist">
          {VIEWS.map((v) => (
            <button key={v.key} role="tab" aria-selected={view === v.key} className={view === v.key ? "on" : ""}
              onClick={() => { setView(v.key); setPage(0); }}>
              {v.label}{v.count && counts[v.count] ? <span className="tab-count">{counts[v.count]}</span> : null}
            </button>
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
              {isToday && <th>Appt</th>}
              <th>Patient</th><th>DOB</th><th>Status</th><th>Alerts</th>
              {locations.length > 1 && <th>Location</th>}
              <th>{view === "completed" || view === "review" ? "Submitted" : isToday ? "Link expires" : "Created"}</th><th />
            </tr>
          </thead>
          <tbody>
            {data?.items.map((i) => (
              <tr key={i.id} className={i.alerts.some((a) => a.level === "high") ? "has-alert" : ""}>
                {isToday && <td className="nowrap"><strong>{i.appointment_at ? fmtTime(i.appointment_at) : "Walk-in"}</strong></td>}
                <td>
                  <Link to={`/staff/intakes/${i.id}`}>{i.patient.last_name}, {i.patient.first_name}</Link>
                  {i.prefilled && <span className="tag" title="Pre-filled from a previous visit">returning</span>}
                </td>
                <td className="nowrap">{fmtDob(i.patient.dob)}</td>
                <td><StatusBadge i={i} /></td>
                <td><Alerts alerts={i.alerts} /></td>
                {locations.length > 1 && <td>{i.location.name}</td>}
                <td className="nowrap">
                  {view === "completed" || view === "review" ? fmtDate(i.submitted_at) : isToday ? fmtDate(i.expires_at) : fmtDate(i.created_at)}
                  {!isToday && view !== "completed" && view !== "review" && <div className="muted small">by {i.created_by}</div>}
                </td>
                <td className="nowrap"><Link to={`/staff/intakes/${i.id}`}>Open →</Link></td>
              </tr>
            ))}
            {data && data.items.length === 0 && (
              <tr><td colSpan={8} className="muted center">
                {isToday ? "No patients on today's list yet. Press N or click “New patient intake” to add one." : "Nothing here."}
              </td></tr>
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

export function LinkPanel({ link, patientName, officeName, onDone, onNext }: {
  link: string; patientName?: string; officeName?: string; onDone: () => void; onNext?: () => void;
}) {
  const [copied, setCopied] = useState<"link" | "message" | null>(null);
  const [qr, setQr] = useState<string | null>(null);

  useEffect(() => {
    QRCode.toDataURL(link, { margin: 1, width: 220, errorCorrectionLevel: "M" }).then(setQr).catch(() => setQr(null));
  }, [link]);

  const message = `Hi${patientName ? ` ${patientName}` : ""}, please complete your new-patient forms for ${officeName ?? "our office"} before your visit: ${link}\nYou'll confirm your date of birth to open them. The link expires in 7 days.`;

  const copy = async (what: "link" | "message") => {
    await navigator.clipboard.writeText(what === "link" ? link : message);
    setCopied(what);
  };

  return (
    <div className="link-panel">
      <p><strong>Link ready.</strong> Send it now. For security it is shown only once. If it's lost, use “Send new link” on the patient's page. It expires in 7 days.</p>
      <div className="link-grid">
        {qr && <figure className="qr"><img src={qr} alt="QR code for the patient link" /><figcaption>In the office? Have the patient scan this with their phone camera.</figcaption></figure>}
        <div className="link-actions">
          <div className="link-box">
            <input readOnly value={link} onFocus={(e) => e.target.select()} aria-label="Patient link" />
          </div>
          <button className="primary" onClick={() => copy("message")}>{copied === "message" ? "Message copied ✓" : "Copy text message"}</button>
          <button className="secondary" onClick={() => copy("link")}>{copied === "link" ? "Link copied ✓" : "Copy link only"}</button>
          <a className="secondary button-like" href={link} target="_blank" rel="noreferrer">Open on this device (tablet)</a>
          <p className="muted small">Don't add health details to the message. The patient confirms their date of birth to open the form.</p>
        </div>
      </div>
      <div className="nav-row">
        <button className="secondary" onClick={onDone}>Done</button>
        {onNext && <button className="primary" onClick={onNext} autoFocus>Next patient →</button>}
      </div>
    </div>
  );
}

interface Lookup { returning: boolean; visits?: number; last_submitted_at?: string; last_location?: string }

function NewIntake({ locations, defaultLocation, onClose, onCreated }: {
  locations: { id: string; name: string }[]; defaultLocation?: string; onClose: () => void; onCreated: () => void;
}) {
  const { call } = useStaff();
  const blank = (keep?: { language: string; location_id: string; appt_date: string }) => ({
    first_name: "", last_name: "", dob: "", appt_time: "",
    appt_date: keep?.appt_date ?? officeToday(),
    language: keep?.language ?? "en", location_id: keep?.location_id ?? defaultLocation ?? "", prefill: true,
  });
  const [form, setForm] = useState(blank());
  const [created, setCreated] = useState<{ link: string; name: string } | null>(null);
  const [lookup, setLookup] = useState<Lookup | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const firstRef = useRef<HTMLInputElement>(null);
  const [createdCount, setCreatedCount] = useState(0);

  // Detect returning patients as soon as name + DOB are filled in.
  useEffect(() => {
    setLookup(null);
    if (!form.first_name.trim() || !form.last_name.trim() || !/^\d{4}-\d{2}-\d{2}$/.test(form.dob)) return;
    const t = window.setTimeout(() => {
      const p = new URLSearchParams({ first_name: form.first_name, last_name: form.last_name, dob: form.dob });
      call<Lookup>(`/api/staff/patients/lookup?${p}`).then(setLookup).catch(() => undefined);
    }, 400);
    return () => window.clearTimeout(t);
  }, [call, form.first_name, form.last_name, form.dob]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      let appointment_at: string | null = null;
      if (form.appt_time) {
        // Sent without an offset: the server reads it in the office time zone.
        appointment_at = `${form.appt_date}T${form.appt_time}`;
      } else if (form.appt_date !== officeToday()) {
        throw new Error("Add an appointment time for a future date (or leave today's date for a walk-in).");
      }
      const r = await call<{ link: string }>("/api/staff/intakes", {
        method: "POST",
        body: {
          first_name: form.first_name, last_name: form.last_name, dob: form.dob, language: form.language,
          location_id: form.location_id, appointment_at, prefill: form.prefill,
        },
      });
      setCreated({ link: r.link, name: form.first_name.trim() });
      setCreatedCount((c) => c + 1);
      onCreated();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const next = () => {
    setCreated(null);
    setForm(blank({ language: form.language, location_id: form.location_id, appt_date: form.appt_date }));
    setTimeout(() => firstRef.current?.focus(), 0);
  };

  const officeName = locations.find((l) => l.id === form.location_id)?.name;

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="New patient intake">
      <div className="modal">
        <div className="card-head">
          <h2>New patient intake</h2>
          {createdCount > 0 && <span className="muted small">{createdCount} created this session</span>}
        </div>
        {created ? (
          <LinkPanel link={created.link} patientName={created.name} officeName={officeName} onDone={onClose} onNext={next} />
        ) : (
          <form onSubmit={submit}>
            <div className="grid-2">
              <label className="field"><span className="field-label">First name</span>
                <input ref={firstRef} autoFocus required maxLength={100} value={form.first_name} onChange={(e) => setForm({ ...form, first_name: e.target.value })} /></label>
              <label className="field"><span className="field-label">Last name</span>
                <input required maxLength={100} value={form.last_name} onChange={(e) => setForm({ ...form, last_name: e.target.value })} /></label>
              <label className="field"><span className="field-label">Date of birth</span>
                <input required type="date" value={form.dob} max={officeToday()} onChange={(e) => setForm({ ...form, dob: e.target.value })} /></label>
              <label className="field"><span className="field-label">Form language</span>
                <select value={form.language} onChange={(e) => setForm({ ...form, language: e.target.value })}>
                  <option value="en">English</option><option value="es">Spanish / Español</option>
                </select></label>
              <label className="field"><span className="field-label">Appointment date</span>
                <input type="date" value={form.appt_date} onChange={(e) => setForm({ ...form, appt_date: e.target.value })} /></label>
              <label className="field"><span className="field-label">Appointment time (optional)</span>
                <input type="time" step={300} value={form.appt_time} onChange={(e) => setForm({ ...form, appt_time: e.target.value })} /></label>
              {locations.length > 1 && (
                <label className="field"><span className="field-label">Location</span>
                  <select required value={form.location_id} onChange={(e) => setForm({ ...form, location_id: e.target.value })}>
                    <option value="" disabled>Select…</option>
                    {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
                  </select></label>
              )}
            </div>
            {lookup?.returning && (
              <div className="returning">
                <strong>Returning patient:</strong> {lookup.visits} previous form{lookup.visits === 1 ? "" : "s"}, last on {fmtDate(lookup.last_submitted_at ?? null)} ({lookup.last_location}).
                <label className="check"><input type="checkbox" checked={form.prefill} onChange={(e) => setForm({ ...form, prefill: e.target.checked })} />
                  Pre-fill their previous answers and insurance card photos. They'll review, update and re-sign.</label>
              </div>
            )}
            <p className="muted small">The patient must enter this date of birth to unlock the form. Five wrong attempts lock the link. With no appointment time, the patient shows as a walk-in on today's list.</p>
            {error && <p className="error">{error}</p>}
            <div className="nav-row">
              <button type="button" className="secondary" onClick={onClose}>Cancel</button>
              <button type="submit" className="primary" disabled={busy || !form.location_id}>Create link</button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
