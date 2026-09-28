import { useCallback, useEffect, useState } from "react";
import { fmtDate } from "./Dashboard";
import { useStaff } from "./StaffApp";

interface Loc { id: string; name: string; address: string; phone: string; active: boolean }
interface Staff {
  id: string; email: string; full_name: string; role: "admin" | "front_desk"; active: boolean;
  linked: boolean; last_login_at: string | null; locations: { id: string; name: string }[];
}

function useLocations() {
  const { call } = useStaff();
  const [locs, setLocs] = useState<Loc[]>([]);
  const reload = useCallback(() => call<Loc[]>("/api/admin/locations").then(setLocs), [call]);
  useEffect(() => { reload(); }, [reload]);
  return { locs, reload };
}

function LocationPicker({ locs, value, onChange }: { locs: Loc[]; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="checkbox-grid compact">
      {locs.filter((l) => l.active).map((l) => (
        <label key={l.id} className="check">
          <input type="checkbox" checked={value.includes(l.id)}
            onChange={(e) => onChange(e.target.checked ? [...value, l.id] : value.filter((x) => x !== l.id))} />
          {l.name}
        </label>
      ))}
    </div>
  );
}

export function StaffUsers() {
  const { call, me } = useStaff();
  const { locs } = useLocations();
  const [staff, setStaff] = useState<Staff[]>([]);
  const [form, setForm] = useState({ email: "", full_name: "", role: "front_desk", location_ids: [] as string[] });
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);

  const reload = useCallback(() => call<Staff[]>("/api/admin/staff").then(setStaff), [call]);
  useEffect(() => { reload(); }, [reload]);

  const create = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await call("/api/admin/staff", { method: "POST", body: form });
      setForm({ email: "", full_name: "", role: "front_desk", location_ids: [] });
      reload();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const patch = async (id: string, body: Record<string, unknown>) => {
    try {
      await call(`/api/admin/staff/${id}`, { method: "PATCH", body });
      reload();
    } catch (err) {
      alert((err as Error).message);
    }
  };

  return (
    <>
      <h1>Staff accounts</h1>
      <form className="card" onSubmit={create}>
        <h2>Invite staff member</h2>
        <p className="muted small">They receive an email invitation with a temporary password and must set up an authenticator app (MFA) on first sign-in.</p>
        <div className="grid-3">
          <label className="field"><span className="field-label">Full name</span>
            <input required value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></label>
          <label className="field"><span className="field-label">Work email</span>
            <input required type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></label>
          <label className="field"><span className="field-label">Role</span>
            <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              <option value="front_desk">Front desk</option><option value="admin">Admin</option>
            </select></label>
        </div>
        <span className="field-label">Locations</span>
        <LocationPicker locs={locs} value={form.location_ids} onChange={(v) => setForm({ ...form, location_ids: v })} />
        {error && <p className="error">{error}</p>}
        <button className="primary" type="submit">Send invitation</button>
      </form>

      <div className="table-wrap">
        <table>
          <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Locations</th><th>Last sign-in</th><th>Status</th><th /></tr></thead>
          <tbody>
            {staff.map((s) => (
              <tr key={s.id} className={s.active ? "" : "inactive"}>
                <td>{s.full_name}</td>
                <td>{s.email}</td>
                <td>
                  <select value={s.role} disabled={s.id === me.id} onChange={(e) => patch(s.id, { role: e.target.value })} aria-label="Role">
                    <option value="front_desk">Front desk</option><option value="admin">Admin</option>
                  </select>
                </td>
                <td>
                  {editing === s.id ? (
                    <LocationPicker locs={locs} value={s.locations.map((l) => l.id)}
                      onChange={(v) => patch(s.id, { location_ids: v })} />
                  ) : (
                    <>{s.locations.map((l) => l.name).join(", ") || "—"}{" "}
                      <button className="link" onClick={() => setEditing(s.id)}>edit</button></>
                  )}
                </td>
                <td>{s.last_login_at ? fmtDate(s.last_login_at) : s.linked ? "—" : "Invited"}</td>
                <td>{s.active ? "Active" : "Disabled"}</td>
                <td>
                  {s.id !== me.id && (
                    <button className="link" onClick={() => patch(s.id, { active: !s.active })}>
                      {s.active ? "Disable" : "Re-enable"}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function Locations() {
  const { call } = useStaff();
  const { locs, reload } = useLocations();
  const blank = { name: "", address: "", phone: "", active: true };
  const [form, setForm] = useState<Omit<Loc, "id">>(blank);
  const [editId, setEditId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      await call(editId ? `/api/admin/locations/${editId}` : "/api/admin/locations", { method: editId ? "PUT" : "POST", body: form });
      setForm(blank);
      setEditId(null);
      reload();
    } catch (err) {
      setError((err as Error).message);
    }
  };

  return (
    <>
      <h1>Office locations</h1>
      <form className="card" onSubmit={save}>
        <h2>{editId ? "Edit location" : "Add location"}</h2>
        <div className="grid-3">
          <label className="field"><span className="field-label">Name</span>
            <input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
          <label className="field"><span className="field-label">Address</span>
            <input value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} /></label>
          <label className="field"><span className="field-label">Phone (shown to patients)</span>
            <input value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} /></label>
        </div>
        <label className="check"><input type="checkbox" checked={form.active} onChange={(e) => setForm({ ...form, active: e.target.checked })} /> Active</label>
        {error && <p className="error">{error}</p>}
        <div className="nav-row">
          {editId && <button type="button" className="secondary" onClick={() => { setEditId(null); setForm(blank); }}>Cancel</button>}
          <button className="primary" type="submit">{editId ? "Save" : "Add location"}</button>
        </div>
      </form>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Name</th><th>Address</th><th>Phone</th><th>Status</th><th /></tr></thead>
          <tbody>
            {locs.map((l) => (
              <tr key={l.id} className={l.active ? "" : "inactive"}>
                <td>{l.name}</td><td>{l.address}</td><td>{l.phone}</td><td>{l.active ? "Active" : "Inactive"}</td>
                <td><button className="link" onClick={() => { setEditId(l.id); setForm({ name: l.name, address: l.address, phone: l.phone, active: l.active }); }}>Edit</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

interface AuditItem {
  id: number; occurred_at: string; actor_type: string; actor_id: string | null; actor_email: string | null;
  action: string; resource_type: string | null; resource_id: string | null; outcome: string;
  ip_address: string | null; details: Record<string, unknown>;
}

export function AuditLog() {
  const { call } = useStaff();
  const [filters, setFilters] = useState({ action: "", actor_email: "", resource_id: "", outcome: "" });
  const [page, setPage] = useState(0);
  const [data, setData] = useState<{ total: number; items: AuditItem[] } | null>(null);
  const size = 100;

  const load = useCallback(() => {
    const params = new URLSearchParams({ limit: String(size), offset: String(page * size) });
    Object.entries(filters).forEach(([k, v]) => v.trim() && params.set(k, v.trim()));
    call<{ total: number; items: AuditItem[] }>(`/api/admin/audit?${params}`).then(setData);
  }, [call, filters, page]);

  useEffect(() => { load(); }, [page]);

  return (
    <>
      <h1>Audit log</h1>
      <p className="muted">Every sign-in, patient-record view, download, and change is recorded here. It cannot be edited or deleted. A copy also goes to CloudWatch Logs.</p>
      <form className="toolbar" onSubmit={(e) => { e.preventDefault(); setPage(0); load(); }}>
        <input placeholder="Action (e.g. intake.pdf)" value={filters.action} onChange={(e) => setFilters({ ...filters, action: e.target.value })} />
        <input placeholder="Staff email" value={filters.actor_email} onChange={(e) => setFilters({ ...filters, actor_email: e.target.value })} />
        <input placeholder="Intake / resource ID" value={filters.resource_id} onChange={(e) => setFilters({ ...filters, resource_id: e.target.value })} />
        <select value={filters.outcome} onChange={(e) => setFilters({ ...filters, outcome: e.target.value })} aria-label="Outcome">
          <option value="">Any outcome</option><option value="success">Success</option>
          <option value="failure">Failure</option><option value="denied">Denied</option>
        </select>
        <button className="secondary" type="submit">Filter</button>
      </form>
      <div className="table-wrap">
        <table className="audit">
          <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Resource</th><th>Outcome</th><th>IP</th><th>Details</th></tr></thead>
          <tbody>
            {data?.items.map((e) => (
              <tr key={e.id} className={e.outcome !== "success" ? "warn" : ""}>
                <td>{fmtDate(e.occurred_at)}</td>
                <td>{e.actor_email ?? e.actor_id ?? e.actor_type}<div className="muted small">{e.actor_type}</div></td>
                <td><code>{e.action}</code></td>
                <td className="small">{e.resource_type} {e.resource_id && <code title={e.resource_id}>{e.resource_id.slice(0, 8)}…</code>}</td>
                <td>{e.outcome}</td>
                <td className="small">{e.ip_address}</td>
                <td className="small"><code>{Object.keys(e.details).length ? JSON.stringify(e.details) : ""}</code></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data && (
        <div className="pager">
          <button className="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>Newer</button>
          <span className="muted">{data.total} events</span>
          <button className="secondary" disabled={(page + 1) * size >= data.total} onClick={() => setPage(page + 1)}>Older</button>
        </div>
      )}
    </>
  );
}
