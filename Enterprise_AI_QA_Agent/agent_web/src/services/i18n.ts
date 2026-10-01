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

// Track a reactive version counter to force re-computation in templates.
const version = ref(0);

/** Register locale messages. Can be called multiple times to merge. */
export function registerLocale(locale: Locale, translations: Record<string, string>): void {
  messages[locale] = { ...(messages[locale] || {}), ...translations };
}

/** Set the active locale. Triggers reactivity for all t() consumers. */
export function setLocale(locale: Locale): void {
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
  return Object.keys(messages);
}
