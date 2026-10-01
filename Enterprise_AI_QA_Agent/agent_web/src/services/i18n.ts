/**
 * Lightweight reactive i18n service for Enterprise AI QA Agent.
 *
 * Uses Vue's reactivity system so components automatically re-render
 * when the locale changes.
 */
import { ref, type Ref } from "vue";

export type Locale = string;

const currentLocale: Ref<Locale> = ref("zh-CN");
const messages: Record<Locale, Record<string, string>> = {};
type LocaleLoader = () => Promise<{ default: Record<string, string> }>;
const loaders: Record<Locale, LocaleLoader> = {};
const pending = new Map<Locale, Promise<boolean>>();
const registeredLocales = new Set<Locale>();

// Track a reactive version counter to force re-computation in templates.
const version = ref(0);

/** Register locale messages. Can be called multiple times to merge. */
export function registerLocale(locale: Locale, translations: Record<string, string>): void {
  registeredLocales.add(locale);
  messages[locale] = { ...(messages[locale] || {}), ...translations };
  // A selected lazy locale must refresh existing computed translations when loaded.
  version.value++;
}

export function registerLocaleLoader(locale: Locale, loader: LocaleLoader): void {
  registeredLocales.add(locale);
  loaders[locale] = loader;
}

/** Single-flight load; on failure retain the existing English/Chinese fallback. */
export function loadLocale(locale: Locale): Promise<boolean> {
  if (messages[locale] || !loaders[locale]) return Promise.resolve(Boolean(messages[locale]));
  const existing = pending.get(locale);
  if (existing) return existing;
  const loading = Promise.resolve().then(loaders[locale]).then((module) => {
    registerLocale(locale, module.default);
    return true;
  }).catch((error: unknown) => {
    console.warn("locale_load_failed", { locale, error });
    return false;
  }).finally(() => pending.delete(locale));
  pending.set(locale, loading);
  return loading;
}

/** Set the active locale. Triggers reactivity for all t() consumers. */
export function setLocale(locale: Locale): void {
  void loadLocale(locale);
  if (currentLocale.value !== locale) {
    currentLocale.value = locale;
    version.value++;
  }
}

/** Get the current active locale. */
export function getLocale(): Locale {
  return currentLocale.value;
}

/**
 * Translate a key. Reactive: components using t() will re-render on locale change.
 * Falls back to en-US, then zh-CN, then to the key itself.
 */
export function t(key: string, params?: Record<string, string | number>): string {
  // Access reactive refs to establish dependency tracking.
  const locale = currentLocale.value;
  const _v = version.value; // eslint-disable-line @typescript-eslint/no-unused-vars

  let text = messages[locale]?.[key] || messages["en-US"]?.[key] || messages["zh-CN"]?.[key] || key;
  if (params) {
    for (const [param, value] of Object.entries(params)) {
      text = text.split(`{${param}}`).join(String(value));
    }
  }
  return text;
}

/** Get all available registered locales. */
export function getAvailableLocales(): Locale[] {
  return [...registeredLocales];
}
