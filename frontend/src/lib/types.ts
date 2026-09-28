export type Lang = "en" | "es";
export type Localized = Record<Lang, string>;

export interface Option {
  value: string;
  label: Localized;
}

export interface Field {
  key: string;
  type: "text" | "textarea" | "date" | "select" | "yesno" | "checkboxes" | "email" | "tel" | "list" | "file";
  label: Localized;
  required?: boolean;
  readonly?: boolean;
  max?: number;
  pattern?: string;
  options?: Option[];
  show_if?: { field: string; equals: string };
  item_fields?: Field[];
  max_items?: number;
  add_label?: Localized;
}

export interface Section {
  key: string;
  title: Localized;
  fields: Field[];
}

export interface Consent {
  key: string;
  title: Localized;
  body: Record<Lang, string[]>;
}

export interface FormDefinition {
  version: string;
  sections: Section[];
  consents: Consent[];
  ui: {
    signer_relationship: { label: Localized; options: Option[] };
    agree: Localized;
    typed_name: Localized;
    draw_signature: Localized;
  };
}

export type Answers = Record<string, unknown>;

export interface ConsentValue {
  agreed: boolean;
  typed_name: string;
  relationship: string;
  signature: string | null;
}

export interface FieldError {
  field: string;
  message: string;
}

export interface IntakeSummary {
  id: string;
  patient: { first_name: string; last_name: string; dob: string };
  status: "pending" | "in_progress" | "submitted" | "expired" | "locked" | "cancelled";
  language: Lang;
  location: { id: string; name: string };
  created_by: string;
  created_at: string;
  expires_at: string;
  first_opened_at: string | null;
  submitted_at: string | null;
  dob_failed_attempts: number;
}

export interface IntakeDetail extends IntakeSummary {
  is_draft: boolean;
  answers: Answers;
  consents: Record<string, { agreed: boolean; typed_name: string; relationship: string; text_sha256?: string; language?: string; has_signature: boolean }>;
  signature_meta: { signed_at: string; ip: string | null; user_agent: string | null } | null;
  form_version: string | null;
  files: { id: string; kind: string; size_bytes: number; created_at: string }[];
  has_pdf: boolean;
}

export interface StaffMe {
  id: string;
  email: string;
  full_name: string;
  role: "admin" | "front_desk";
  locations: { id: string; name: string }[];
}

export function isVisible(field: Field, answers: Answers, all: Field[]): boolean {
  if (!field.show_if) return true;
  const parent = all.find((f) => f.key === field.show_if!.field);
  if (parent && !isVisible(parent, answers, all)) return false;
  return answers[field.show_if.field] === field.show_if.equals;
}
