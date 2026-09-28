import type { Lang } from "./types";

const strings = {
  welcome: { en: "Welcome", es: "Bienvenido(a)" },
  verifyIntro: {
    en: "To protect your privacy, please confirm your date of birth to open your new-patient forms.",
    es: "Para proteger su privacidad, confirme su fecha de nacimiento para abrir sus formularios de paciente nuevo.",
  },
  dob: { en: "Date of birth", es: "Fecha de nacimiento" },
  continue: { en: "Continue", es: "Continuar" },
  back: { en: "Back", es: "Atrás" },
  next: { en: "Next", es: "Siguiente" },
  review: { en: "Review & sign", es: "Revisar y firmar" },
  submit: { en: "Submit forms", es: "Enviar formularios" },
  submitting: { en: "Submitting…", es: "Enviando…" },
  saving: { en: "Saving…", es: "Guardando…" },
  saved: { en: "Progress saved", es: "Progreso guardado" },
  saveFailed: { en: "Could not save. Check your connection.", es: "No se pudo guardar. Revise su conexión." },
  required: { en: "Required", es: "Obligatorio" },
  yes: { en: "Yes", es: "Sí" },
  no: { en: "No", es: "No" },
  select: { en: "Select…", es: "Seleccione…" },
  remove: { en: "Remove", es: "Eliminar" },
  uploadPhoto: { en: "Take or choose photo", es: "Tomar o elegir foto" },
  replacePhoto: { en: "Replace photo", es: "Reemplazar foto" },
  uploaded: { en: "Uploaded", es: "Subida" },
  uploading: { en: "Uploading…", es: "Subiendo…" },
  clear: { en: "Clear", es: "Borrar" },
  consentsTitle: { en: "Consent forms", es: "Formularios de consentimiento" },
  fixErrors: { en: "Please correct the following:", es: "Por favor corrija lo siguiente:" },
  doneTitle: { en: "Thank you!", es: "¡Gracias!" },
  doneBody: {
    en: "Your forms have been submitted securely. We look forward to seeing you. You may now close this page.",
    es: "Sus formularios se enviaron de forma segura. Esperamos verle pronto. Ya puede cerrar esta página.",
  },
  sessionExpired: {
    en: "For your security, your session timed out. Please confirm your date of birth again. Your progress was saved.",
    es: "Por su seguridad, su sesión expiró. Confirme de nuevo su fecha de nacimiento. Su progreso se guardó.",
  },
  noLink: {
    en: "This page needs the personal link sent to you by our office.",
    es: "Esta página requiere el enlace personal que le envió nuestra oficina.",
  },
  step: { en: "Step", es: "Paso" },
  of: { en: "of", es: "de" },
  privacyNote: {
    en: "Your information is encrypted and only shared with our dental team.",
    es: "Su información está cifrada y solo se comparte con nuestro equipo dental.",
  },
  linkExpires: { en: "This link expires", es: "Este enlace vence el" },
  questions: { en: "Questions? Call us at", es: "¿Preguntas? Llámenos al" },
  prefilled: {
    en: "Welcome back! We filled in your answers from your last visit. Please check each page, update anything that changed (medications, insurance, health), and sign the consent forms again.",
    es: "¡Bienvenido(a) de nuevo! Completamos sus respuestas de su última visita. Revise cada página, actualice lo que haya cambiado (medicamentos, seguro, salud) y vuelva a firmar los consentimientos.",
  },
  handBack: {
    en: "If you are using an office tablet, please return it to the front desk.",
    es: "Si está usando una tableta de la oficina, devuélvala a la recepción.",
  },
} satisfies Record<string, Record<Lang, string>>;

export type StringKey = keyof typeof strings;

export function t(key: StringKey, lang: Lang): string {
  return strings[key][lang];
}
