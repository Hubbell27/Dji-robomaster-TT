import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api } from "../lib/api";
import { t } from "../lib/i18n";
import type { Answers, ConsentValue, Field, FieldError, FormDefinition, Lang } from "../lib/types";
import { isVisible } from "../lib/types";
import { FieldInput } from "./FieldInput";
import { SignaturePad } from "./SignaturePad";
import { prepareCardPhoto } from "./photo";

const LINK_KEY = "intake.link";
const SESSION_KEY = "intake.session";

interface SessionInfo {
  session_token: string;
  first_name: string;
  language: Lang;
  location: { name: string; phone: string };
  expires_at: string;
}

function storage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

const LANG_KEY = "intake.lang";

function initialLang(): Lang {
  const m = window.location.hash.match(/[#&]l=(en|es)\b/);
  if (m) {
    storage()?.setItem(LANG_KEY, m[1]);
    return m[1] as Lang;
  }
  const saved = storage()?.getItem(LANG_KEY);
  if (saved === "en" || saved === "es") return saved;
  return navigator.language.startsWith("es") ? "es" : "en";
}

function readLinkToken(): string | null {
  const m = window.location.hash.match(/[#&]t=([A-Za-z0-9_-]+)/);
  if (m) {
    storage()?.setItem(LINK_KEY, m[1]);
    // Remove the secret from the address bar and browser history entry.
    window.history.replaceState(null, "", window.location.pathname);
    return m[1];
  }
  return storage()?.getItem(LINK_KEY) ?? null;
}

export function PatientApp() {
  const [lang, setLang] = useState<Lang>(initialLang);
  const [linkToken] = useState(readLinkToken);
  const [def, setDef] = useState<FormDefinition | null>(null);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [phase, setPhase] = useState<"verify" | "form" | "done">("verify");
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    api<FormDefinition>("/api/patient/form-definition").then(setDef).catch(() => setNotice("Unable to load form."));
  }, []);

  useEffect(() => {
    const saved = storage()?.getItem(SESSION_KEY);
    if (saved) {
      try {
        const s = JSON.parse(saved) as SessionInfo;
        setSession(s);
        setLang(s.language);
        setPhase("form");
      } catch {
        storage()?.removeItem(SESSION_KEY);
      }
    }
  }, []);

  const onVerified = (s: SessionInfo) => {
    storage()?.setItem(SESSION_KEY, JSON.stringify(s));
    setSession(s);
    setLang(s.language);
    setNotice(null);
    setPhase("form");
  };

  const onSessionLost = useCallback((message: string) => {
    storage()?.removeItem(SESSION_KEY);
    setSession(null);
    setNotice(message);
    setPhase("verify");
  }, []);

  const onDone = () => {
    storage()?.removeItem(SESSION_KEY);
    storage()?.removeItem(LINK_KEY);
    setPhase("done");
  };

  return (
    <div className="patient">
      <header className="patient-header">
        <div className="brand">{session?.location.name ?? "New Patient Forms"}</div>
        <div className="lang-toggle" role="group" aria-label="Language / Idioma">
          <button type="button" className={lang === "en" ? "on" : ""} onClick={() => setLang("en")}>English</button>
          <button type="button" className={lang === "es" ? "on" : ""} onClick={() => setLang("es")}>Español</button>
        </div>
      </header>
      <main className="patient-main">
        {phase === "done" ? (
          <div className="card center">
            <h1>{t("doneTitle", lang)}</h1>
            <p>{t("doneBody", lang)}</p>
            <p className="muted">{t("handBack", lang)}</p>
          </div>
        ) : !linkToken && !session ? (
          <div className="card center"><p>{t("noLink", lang)}</p></div>
        ) : phase === "verify" || !session ? (
          <Verify linkToken={linkToken!} lang={lang} notice={notice} onVerified={onVerified} />
        ) : def ? (
          <IntakeForm def={def} session={session} setSession={(s) => { setSession(s); storage()?.setItem(SESSION_KEY, JSON.stringify(s)); }}
            lang={lang} setLang={setLang} onSessionLost={onSessionLost} onDone={onDone} />
        ) : (
          <div className="card center">…</div>
        )}
      </main>
      <footer className="patient-footer">🔒 {t("privacyNote", lang)}</footer>
    </div>
  );
}

function Verify({ linkToken, lang, notice, onVerified }: {
  linkToken: string; lang: Lang; notice: string | null; onVerified: (s: SessionInfo) => void;
}) {
  const [dob, setDob] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [blocked, setBlocked] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onVerified(await api<SessionInfo>("/api/patient/verify", { method: "POST", body: { token: linkToken, dob } }));
    } catch (err) {
      const ae = err as ApiError;
      setError(ae.message);
      if ([404, 410, 423].includes(ae.status)) setBlocked(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card narrow" onSubmit={submit}>
      <h1>{t("welcome", lang)}</h1>
      {notice && <p className="notice">{notice}</p>}
      <p>{t("verifyIntro", lang)}</p>
      <div className="field">
        <label htmlFor="dob" className="field-label">{t("dob", lang)}</label>
        <input id="dob" type="date" required value={dob} onChange={(e) => setDob(e.target.value)} disabled={blocked} autoComplete="bday" />
      </div>
      {error && <p className="error" role="alert">{error}</p>}
      <button type="submit" className="primary" disabled={busy || blocked || !dob}>{t("continue", lang)}</button>
    </form>
  );
}

type SaveState = "idle" | "saving" | "saved" | "error";

function IntakeForm({ def, session, setSession, lang, setLang, onSessionLost, onDone }: {
  def: FormDefinition;
  session: SessionInfo;
  setSession: (s: SessionInfo) => void;
  lang: Lang;
  setLang: (l: Lang) => void;
  onSessionLost: (msg: string) => void;
  onDone: () => void;
}) {
  const [answers, setAnswers] = useState<Answers | null>(null);
  const [prefilled, setPrefilled] = useState(false);
  const [files, setFiles] = useState<Record<string, string>>({});
  const [consents, setConsents] = useState<Record<string, ConsentValue>>(() =>
    Object.fromEntries(def.consents.map((c) => [c.key, { agreed: false, typed_name: "", relationship: "self", signature: null }])),
  );
  const [step, setStep] = useState(0);
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [submitting, setSubmitting] = useState(false);
  const tokenRef = useRef(session.session_token);
  const dirty = useRef(false);
  const allFields = useMemo(() => def.sections.flatMap((s) => s.fields), [def]);
  const steps = def.sections.length + 1;
  const topRef = useRef<HTMLDivElement>(null);

  const handleAuthError = useCallback((err: unknown) => {
    if (err instanceof ApiError && err.status === 401) {
      onSessionLost(t("sessionExpired", lang));
      return true;
    }
    return false;
  }, [lang, onSessionLost]);

  const authErrorRef = useRef(handleAuthError);
  authErrorRef.current = handleAuthError;
  const latest = useRef({ answers, lang });
  latest.current = { answers, lang };

  // Load the saved draft once. (Re-running this would overwrite unsaved edits.)
  useEffect(() => {
    api<SessionInfo & { answers: Answers; prefilled: boolean; files: { id: string; kind: string }[] }>("/api/patient/intake", { token: tokenRef.current })
      .then((r) => {
        tokenRef.current = r.session_token;
        setAnswers(r.answers);
        setPrefilled(r.prefilled);
        setFiles(Object.fromEntries(r.files.map((f) => [f.kind, f.id])));
      })
      .catch((err) => authErrorRef.current(err));
  }, []);

  const saveDraft = useCallback(async () => {
    const { answers: current, lang: currentLang } = latest.current;
    if (!current || !dirty.current) return;
    dirty.current = false;
    setSaveState("saving");
    try {
      const r = await api<{ session_token: string }>("/api/patient/intake/draft", {
        method: "PUT", token: tokenRef.current, body: { answers: current, language: currentLang },
      });
      tokenRef.current = r.session_token;
      setSession({ ...session, session_token: r.session_token, language: currentLang });
      setSaveState("saved");
    } catch (err) {
      dirty.current = true;
      if (!authErrorRef.current(err)) setSaveState("error");
    }
  }, [session, setSession]);

  // Debounced autosave while typing; navigation between steps saves immediately.
  useEffect(() => {
    if (!answers || !dirty.current) return;
    const timer = window.setTimeout(saveDraft, 1500);
    return () => window.clearTimeout(timer);
  }, [answers, lang, saveDraft]);

  if (!answers) return <div className="card center">…</div>;

  const set = (key: string, value: unknown) => {
    dirty.current = true;
    setAnswers({ ...answers, [key]: value });
    setErrors((e) => e.filter((x) => x.field.split(".")[0] !== key));
  };

  const errorFor = (key: string) =>
    errors.filter((e) => e.field === key || e.field.startsWith(`${key}.`)).map((e) => e.message).join("; ") || undefined;

  const missingRequired = (fields: Field[]): FieldError[] =>
    fields
      .filter((f) => f.required && isVisible(f, answers, allFields))
      .filter((f) => {
        if (f.type === "file") return !files[f.key];
        const v = answers[f.key];
        return v === undefined || v === "" || (Array.isArray(v) && v.length === 0);
      })
      .map((f) => ({ field: f.key, message: `${f.label[lang]}: ${t("required", lang)}` }));

  const goto = (n: number) => {
    void saveDraft();
    setStep(n);
    topRef.current?.scrollIntoView({ behavior: "smooth" });
  };

  const next = () => {
    const missing = missingRequired(def.sections[step].fields);
    setErrors(missing);
    if (missing.length === 0) goto(step + 1);
  };

  const submit = async () => {
    setSubmitting(true);
    setErrors([]);
    try {
      await api("/api/patient/intake/submit", {
        method: "POST", token: tokenRef.current, body: { answers, consents, language: lang },
      });
      onDone();
    } catch (err) {
      if (handleAuthError(err)) return;
      const ae = err as ApiError;
      setErrors(ae.errors.length ? ae.errors : [{ field: "_", message: ae.message }]);
      const firstBad = def.sections.findIndex((s) => s.fields.some((f) => ae.errors.some((e) => e.field.split(".")[0] === f.key)));
      if (firstBad >= 0) goto(firstBad);
    } finally {
      setSubmitting(false);
    }
  };

  const section = def.sections[step];
  return (
    <div ref={topRef}>
      <div className="progress" aria-label={`${t("step", lang)} ${step + 1} ${t("of", lang)} ${steps}`}>
        {Array.from({ length: steps }, (_, i) => (
          <span key={i} className={i <= step ? "on" : ""} />
        ))}
      </div>
      <div className="card">
        <div className="card-head">
          <h1>{section ? section.title[lang] : t("consentsTitle", lang)}</h1>
          <span className={`save-state ${saveState}`} aria-live="polite">
            {saveState === "saving" ? t("saving", lang) : saveState === "saved" ? t("saved", lang) : saveState === "error" ? t("saveFailed", lang) : ""}
          </span>
        </div>

        {prefilled && step === 0 && <p className="notice">{t("prefilled", lang)}</p>}

        {errors.length > 0 && (
          <div className="error-box" role="alert">
            <strong>{t("fixErrors", lang)}</strong>
            <ul>{errors.slice(0, 10).map((e) => <li key={e.field}>{e.message}</li>)}</ul>
          </div>
        )}

        {section ? (
          section.fields.filter((f) => isVisible(f, answers, allFields)).map((f) =>
            f.type === "file" ? (
              <PhotoField key={f.key} field={f} lang={lang} fileId={files[f.key]} error={errorFor(f.key)}
                token={() => tokenRef.current}
                onUploaded={(id) => { setFiles({ ...files, [f.key]: id }); setErrors((e) => e.filter((x) => x.field !== f.key)); }}
                onAuthError={handleAuthError} />
            ) : (
              <FieldInput key={f.key} field={f} lang={lang} value={answers[f.key]} error={errorFor(f.key)} onChange={(v) => set(f.key, v)} />
            ),
          )
        ) : (
          def.consents.map((c) => (
            <section key={c.key} className="consent">
              <h2>{c.title[lang]}</h2>
              {c.body[lang].map((p, i) => <p key={i}>{p}</p>)}
              <ConsentSigner def={def} lang={lang} value={consents[c.key]} error={errorFor(`consent.${c.key}`)}
                onChange={(v) => setConsents({ ...consents, [c.key]: v })} />
            </section>
          ))
        )}

        <div className="nav-row">
          {step > 0 && <button type="button" className="secondary" onClick={() => goto(step - 1)}>{t("back", lang)}</button>}
          {step < steps - 1 ? (
            <button type="button" className="primary" onClick={next}>
              {step === steps - 2 ? t("review", lang) : t("next", lang)}
            </button>
          ) : (
            <button type="button" className="primary" disabled={submitting} onClick={submit}>
              {submitting ? t("submitting", lang) : t("submit", lang)}
            </button>
          )}
        </div>
      </div>
      <p className="muted small center">
        {t("linkExpires", lang)} {new Date(session.expires_at).toLocaleDateString(lang)}.{" "}
        {session.location.phone && <>{t("questions", lang)} {session.location.phone}.</>}
        {" "}<button type="button" className="link" onClick={() => setLang(lang === "en" ? "es" : "en")}>{lang === "en" ? "Español" : "English"}</button>
      </p>
    </div>
  );
}

function ConsentSigner({ def, lang, value, error, onChange }: {
  def: FormDefinition; lang: Lang; value: ConsentValue; error?: string; onChange: (v: ConsentValue) => void;
}) {
  return (
    <div className={`signer ${error ? "has-error" : ""}`}>
      <label className="check strong">
        <input type="checkbox" checked={value.agreed} onChange={(e) => onChange({ ...value, agreed: e.target.checked })} />
        {def.ui.agree[lang]}
      </label>
      <div className="signer-grid">
        <div className="field">
          <label className="field-label">{def.ui.signer_relationship.label[lang]}</label>
          <select value={value.relationship} onChange={(e) => onChange({ ...value, relationship: e.target.value })}>
            {def.ui.signer_relationship.options.map((o) => <option key={o.value} value={o.value}>{o.label[lang]}</option>)}
          </select>
        </div>
        <div className="field">
          <label className="field-label">{def.ui.typed_name[lang]}</label>
          <input type="text" maxLength={200} value={value.typed_name} autoComplete="name"
            onChange={(e) => onChange({ ...value, typed_name: e.target.value })} />
        </div>
      </div>
      <div className="field">
        <span className="field-label">{def.ui.draw_signature[lang]}</span>
        <SignaturePad value={value.signature} ariaLabel={def.ui.draw_signature[lang]} clearLabel={t("clear", lang)}
          onChange={(sig) => onChange({ ...value, signature: sig })} />
      </div>
      {error && <p className="error">{error}</p>}
    </div>
  );
}

function PhotoField({ field, lang, fileId, error, token, onUploaded, onAuthError }: {
  field: Field; lang: Lang; fileId?: string; error?: string; token: () => string;
  onUploaded: (id: string) => void; onAuthError: (e: unknown) => boolean;
}) {
  const [preview, setPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => () => { if (preview) URL.revokeObjectURL(preview); }, [preview]);

  const pick = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    setBusy(true);
    setErr(null);
    try {
      const jpeg = await prepareCardPhoto(file).catch(() => file);
      const form = new FormData();
      form.append("file", jpeg, "card.jpg");
      const r = await api<{ id: string }>(`/api/patient/intake/files/${field.key}`, { method: "POST", token: token(), form });
      setPreview(URL.createObjectURL(jpeg));
      onUploaded(r.id);
    } catch (e2) {
      if (!onAuthError(e2)) setErr((e2 as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={`field photo ${error || err ? "has-error" : ""}`}>
      <span className="field-label">{field.label[lang]}{field.required && <span className="req"> *</span>}</span>
      <div className="photo-row">
        {preview ? <img src={preview} alt="" /> : fileId ? <div className="photo-done">✓ {t("uploaded", lang)}</div> : null}
        <label className="secondary file-btn">
          {busy ? t("uploading", lang) : fileId ? t("replacePhoto", lang) : t("uploadPhoto", lang)}
          <input type="file" accept="image/*" capture="environment" onChange={pick} disabled={busy} />
        </label>
      </div>
      {(error || err) && <p className="error">{err ?? error}</p>}
    </div>
  );
}
