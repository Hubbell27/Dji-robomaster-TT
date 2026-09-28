import { useState } from "react";
import { t } from "../lib/i18n";
import type { Answers, Field, Lang } from "../lib/types";

interface FieldProps {
  field: Field;
  value: unknown;
  lang: Lang;
  error?: string;
  onChange: (value: unknown) => void;
}

export function FieldInput({ field, value, lang, error, onChange }: FieldProps) {
  const id = `f-${field.key}`;
  const label = (
    <label htmlFor={id} className="field-label">
      {field.label[lang]}
      {field.required && <span className="req" aria-label={t("required", lang)}> *</span>}
    </label>
  );
  const str = typeof value === "string" ? value : "";
  const common = {
    id,
    "aria-invalid": error ? true : undefined,
    "aria-describedby": error ? `${id}-err` : undefined,
    disabled: field.readonly,
  };

  let control: React.ReactNode;
  switch (field.type) {
    case "textarea":
      control = <textarea {...common} rows={3} maxLength={field.max} value={str} onChange={(e) => onChange(e.target.value)} />;
      break;
    case "date":
      control = <input {...common} type="date" value={str} max={new Date().toISOString().slice(0, 10)} onChange={(e) => onChange(e.target.value)} />;
      break;
    case "email":
      control = <input {...common} type="email" autoComplete="email" maxLength={320} value={str} onChange={(e) => onChange(e.target.value)} />;
      break;
    case "tel":
      control = <input {...common} type="tel" autoComplete="tel" maxLength={25} value={str} onChange={(e) => onChange(e.target.value)} />;
      break;
    case "select":
      control = (
        <select {...common} value={str} onChange={(e) => onChange(e.target.value)}>
          <option value="">{t("select", lang)}</option>
          {field.options!.map((o) => (
            <option key={o.value} value={o.value}>{o.label[lang]}</option>
          ))}
        </select>
      );
      break;
    case "yesno":
      return (
        <fieldset className={`field ${error ? "has-error" : ""}`}>
          <legend className="field-label">
            {field.label[lang]}
            {field.required && <span className="req"> *</span>}
          </legend>
          <div className="choice-row">
            {(["yes", "no"] as const).map((v) => (
              <label key={v} className={`chip ${str === v ? "on" : ""}`}>
                <input type="radio" name={id} value={v} checked={str === v} onChange={() => onChange(v)} />
                {t(v, lang)}
              </label>
            ))}
          </div>
          {error && <p className="error" id={`${id}-err`}>{error}</p>}
        </fieldset>
      );
    case "checkboxes": {
      const selected = Array.isArray(value) ? (value as string[]) : [];
      return (
        <fieldset className="field">
          <legend className="field-label">{field.label[lang]}</legend>
          <div className="checkbox-grid">
            {field.options!.map((o) => (
              <label key={o.value} className="check">
                <input
                  type="checkbox"
                  checked={selected.includes(o.value)}
                  onChange={(e) =>
                    onChange(e.target.checked ? [...selected, o.value] : selected.filter((v) => v !== o.value))
                  }
                />
                {o.label[lang]}
              </label>
            ))}
          </div>
        </fieldset>
      );
    }
    case "list":
      return <ListInput field={field} value={value} lang={lang} error={error} onChange={onChange} />;
    default:
      control = (
        <input {...common} type="text" maxLength={field.max} value={str} autoComplete={autoComplete(field.key)} onChange={(e) => onChange(e.target.value)} />
      );
  }

  return (
    <div className={`field ${error ? "has-error" : ""}`}>
      {label}
      {control}
      {error && <p className="error" id={`${id}-err`}>{error}</p>}
    </div>
  );
}

function autoComplete(key: string): string | undefined {
  return ({
    first_name: "given-name",
    middle_name: "additional-name",
    last_name: "family-name",
    address1: "address-line1",
    address2: "address-line2",
    city: "address-level2",
    state: "address-level1",
    zip: "postal-code",
  } as Record<string, string>)[key];
}

function ListInput({ field, value, lang, error, onChange }: FieldProps) {
  const items = Array.isArray(value) ? (value as Answers[]) : [];
  const [rows, setRows] = useState<Answers[]>(items.length ? items : [{}]);
  const update = (next: Answers[]) => {
    setRows(next);
    onChange(next.filter((r) => Object.values(r).some((v) => v)));
  };
  return (
    <fieldset className={`field list ${error ? "has-error" : ""}`}>
      <legend className="field-label">
        {field.label[lang]}
        {field.required && <span className="req"> *</span>}
      </legend>
      {rows.map((row, i) => (
        <div className="list-row" key={i}>
          {field.item_fields!.map((sub) => (
            <FieldInput
              key={sub.key}
              field={{ ...sub, key: `${field.key}-${i}-${sub.key}` }}
              value={row[sub.key]}
              lang={lang}
              onChange={(v) => update(rows.map((r, j) => (j === i ? { ...r, [sub.key]: v } : r)))}
            />
          ))}
          {rows.length > 1 && (
            <button type="button" className="link danger" onClick={() => update(rows.filter((_, j) => j !== i))}>
              {t("remove", lang)}
            </button>
          )}
        </div>
      ))}
      {rows.length < (field.max_items ?? 50) && (
        <button type="button" className="secondary" onClick={() => update([...rows, {}])}>
          + {field.add_label?.[lang]}
        </button>
      )}
      {error && <p className="error">{error}</p>}
    </fieldset>
  );
}
