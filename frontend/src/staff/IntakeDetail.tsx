import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { Field, FormDefinition, IntakeDetail } from "../lib/types";
import { isVisible } from "../lib/types";
import { LinkPanel, STATUS_LABEL, fmtDate, fmtDob } from "./Dashboard";
import { useStaff } from "./StaffApp";

function display(field: Field, value: unknown): string {
  if (value === undefined || value === null || value === "" || (Array.isArray(value) && !value.length))
    return field.type === "checkboxes" ? "None" : "—";
  if (field.type === "yesno") return value === "yes" ? "Yes" : "No";
  const opt = (v: string) => field.options?.find((o) => o.value === v)?.label.en ?? v;
  if (field.type === "select") return opt(value as string);
  if (field.type === "checkboxes") return (value as string[]).map(opt).join(", ");
  return String(value);
}

export function IntakeDetailPage() {
  const { id } = useParams();
  const { call, blob } = useStaff();
  const [intake, setIntake] = useState<IntakeDetail | null>(null);
  const [def, setDef] = useState<FormDefinition | null>(null);
  const [images, setImages] = useState<Record<string, string>>({});
  const [link, setLink] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => call<IntakeDetail>(`/api/staff/intakes/${id}`).then(setIntake).catch((e) => setError(e.message));

  useEffect(() => {
    load();
    call<FormDefinition>("/api/staff/form-definition").then(setDef);
  }, [id]);

  useEffect(() => {
    if (!intake) return;
    const urls: string[] = [];
    Promise.all(intake.files.map(async (f) => {
      const url = URL.createObjectURL(await blob(`/api/staff/intakes/${intake.id}/files/${f.id}`));
      urls.push(url);
      return [f.kind, url] as const;
    })).then((pairs) => setImages(Object.fromEntries(pairs)));
    return () => urls.forEach(URL.revokeObjectURL);
  }, [intake, blob]);

  const allFields = useMemo(() => def?.sections.flatMap((s) => s.fields) ?? [], [def]);

  if (error) return <p className="error">{error}</p>;
  if (!intake || !def) return <p>Loading…</p>;

  const downloadPdf = async () => {
    const url = URL.createObjectURL(await blob(`/api/staff/intakes/${intake.id}/pdf`));
    const a = document.createElement("a");
    a.href = url;
    a.download = `intake_${intake.patient.last_name}_${intake.patient.first_name}.pdf`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  };

  const reissue = async () => {
    if (!confirm("Issue a new link? Any previous link for this patient stops working.")) return;
    const r = await call<{ link: string }>(`/api/staff/intakes/${intake.id}/reissue`, { method: "POST" });
    setLink(r.link);
    load();
  };

  const cancel = async () => {
    if (!confirm("Cancel this intake? The patient's link will stop working.")) return;
    await call(`/api/staff/intakes/${intake.id}/cancel`, { method: "POST" });
    load();
  };

  const canReissue = !["submitted", "cancelled"].includes(intake.status);

  return (
    <>
      <p><Link to="/staff">← Back to dashboard</Link></p>
      <div className="page-head">
        <div>
          <h1>{intake.patient.last_name}, {intake.patient.first_name}</h1>
          <p className="muted">
            DOB {fmtDob(intake.patient.dob)} · {intake.location.name} · <span className={`badge ${intake.status}`}>{STATUS_LABEL[intake.status]}</span>
          </p>
        </div>
        <div className="actions">
          {intake.has_pdf && <button className="primary" onClick={downloadPdf}>Download PDF</button>}
          {canReissue && <button className="secondary" onClick={reissue}>{intake.status === "pending" ? "Reissue link" : "Send new link"}</button>}
          {canReissue && <button className="link danger" onClick={cancel}>Cancel intake</button>}
        </div>
      </div>

      {link && <div className="card"><LinkPanel link={link} onDone={() => setLink(null)} /></div>}

      <div className="card meta-grid">
        <div><span className="muted small">Created</span><div>{fmtDate(intake.created_at)} by {intake.created_by}</div></div>
        <div><span className="muted small">First opened</span><div>{fmtDate(intake.first_opened_at)}</div></div>
        <div><span className="muted small">{intake.submitted_at ? "Submitted" : "Link expires"}</span><div>{fmtDate(intake.submitted_at ?? intake.expires_at)}</div></div>
        <div><span className="muted small">Language</span><div>{intake.language === "es" ? "Spanish" : "English"}</div></div>
        {intake.dob_failed_attempts > 0 && <div><span className="muted small">Failed DOB attempts</span><div>{intake.dob_failed_attempts}</div></div>}
      </div>

      {intake.is_draft && (
        <p className="notice">
          {Object.keys(intake.answers).length > 0
            ? "Not yet submitted. Showing the patient's saved draft, which may be incomplete."
            : "The patient has not started the form yet."}
        </p>
      )}

      {def.sections.map((s) => {
        const fields = s.fields.filter((f) => isVisible(f, intake.answers, allFields));
        if (!fields.length || (intake.is_draft && !fields.some((f) => intake.answers[f.key] !== undefined || images[f.key]))) return null;
        return (
          <section className="card" key={s.key}>
            <h2>{s.title.en}</h2>
            <dl className="answers">
              {fields.map((f) =>
                f.type === "file" ? (
                  images[f.key] ? (
                    <div className="answer wide" key={f.key}>
                      <dt>{f.label.en}</dt>
                      <dd><a href={images[f.key]} target="_blank" rel="noreferrer"><img className="card-photo" src={images[f.key]} alt={f.label.en} /></a></dd>
                    </div>
                  ) : null
                ) : f.type === "list" ? (
                  <div className="answer wide" key={f.key}>
                    <dt>{f.label.en}</dt>
                    <dd>
                      {Array.isArray(intake.answers[f.key]) && (intake.answers[f.key] as Record<string, string>[]).length ? (
                        <table className="inner">
                          <thead><tr>{f.item_fields!.map((sf) => <th key={sf.key}>{sf.label.en}</th>)}</tr></thead>
                          <tbody>
                            {(intake.answers[f.key] as Record<string, string>[]).map((row, i) => (
                              <tr key={i}>{f.item_fields!.map((sf) => <td key={sf.key}>{row[sf.key] ? display(sf, row[sf.key]) : ""}</td>)}</tr>
                            ))}
                          </tbody>
                        </table>
                      ) : "—"}
                    </dd>
                  </div>
                ) : (
                  <div className={`answer ${f.type === "textarea" || f.type === "checkboxes" ? "wide" : ""}`} key={f.key}>
                    <dt>{f.label.en}</dt>
                    <dd>{display(f, intake.answers[f.key])}</dd>
                  </div>
                ),
              )}
            </dl>
          </section>
        );
      })}

      {Object.keys(intake.consents).length > 0 && (
        <section className="card">
          <h2>Consents</h2>
          <table className="inner">
            <thead><tr><th>Form</th><th>Agreed</th><th>Signed by</th><th>As</th><th>Signature</th></tr></thead>
            <tbody>
              {def.consents.map((c) => {
                const v = intake.consents[c.key];
                if (!v) return null;
                return (
                  <tr key={c.key}>
                    <td>{c.title.en}</td>
                    <td>{v.agreed ? "Yes" : "No"}</td>
                    <td>{v.typed_name}</td>
                    <td>{def.ui.signer_relationship.options.find((o) => o.value === v.relationship)?.label.en}</td>
                    <td>{v.has_signature ? "Captured (see PDF)" : "Missing"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {intake.signature_meta && (
            <p className="muted small">Signed {intake.signature_meta.signed_at} from {intake.signature_meta.ip ?? "unknown IP"}.</p>
          )}
        </section>
      )}
    </>
  );
}
