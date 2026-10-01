import { computed, nextTick } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";

let i18n: typeof import("./i18n");

beforeEach(async () => {
  vi.resetModules();
  i18n = await import("./i18n");
});

describe("on-demand locale dictionaries", () => {
  it("lists unloaded locales and loads the selected real dictionary once", async () => {
    const english = (await import("../locales/en-US")).default;
    const french = (await import("../locales/fr-FR")).default;
    i18n.registerLocale("en-US", english);
    const loader = vi.fn(() => import("../locales/fr-FR"));
    i18n.registerLocaleLoader("fr-FR", loader);
    expect(i18n.getAvailableLocales()).toEqual(["en-US", "fr-FR"]);
    expect(loader).not.toHaveBeenCalled();
    i18n.setLocale("fr-FR");
    expect(i18n.t("home.title")).toBe(english["home.title"]);
    const first = i18n.loadLocale("fr-FR");
    expect(i18n.loadLocale("fr-FR")).toBe(first);
    expect(await first).toBe(true);
    expect(i18n.t("home.title")).toBe(french["home.title"]);
    expect(loader).toHaveBeenCalledTimes(1);
    i18n.setLocale("en-US");
    i18n.setLocale("fr-FR");
    await i18n.loadLocale("fr-FR");
    expect(loader).toHaveBeenCalledTimes(1);
  });

  it("updates existing reactive translations when the selected dictionary arrives", async () => {
    i18n.registerLocale("en-US", { greeting: "Hello" });
    let finish!: (value: { default: Record<string, string> }) => void;
    i18n.registerLocaleLoader("fr-FR", () => new Promise((resolve) => { finish = resolve; }));
    i18n.setLocale("fr-FR");
    const translated = computed(() => i18n.t("greeting"));
    expect(translated.value).toBe("Hello");
    const loaded = i18n.loadLocale("fr-FR");
    await Promise.resolve();
    finish({ default: { greeting: "Bonjour" } });
    await loaded;
    await nextTick();
    expect(translated.value).toBe("Bonjour");
  });

  it("keeps the newest language selected when an earlier download finishes late", async () => {
    let finish!: (value: { default: Record<string, string> }) => void;
    i18n.registerLocaleLoader("fr-FR", () => new Promise((resolve) => { finish = resolve; }));
    i18n.registerLocaleLoader("ja-JP", () => import("../locales/ja-JP"));
    i18n.setLocale("fr-FR");
    const earlier = i18n.loadLocale("fr-FR");
    i18n.setLocale("ja-JP");
    await i18n.loadLocale("ja-JP");
    const japanese = (await import("../locales/ja-JP")).default;
    finish({ default: { "home.title": "earlier result" } });
    await earlier;
    expect(i18n.getLocale()).toBe("ja-JP");
    expect(i18n.t("home.title")).toBe(japanese["home.title"]);
  });

  it("logs a download failure, retains fallback text and allows a later retry", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
    try {
      i18n.registerLocale("en-US", { greeting: "Hello" });
      const loader = vi.fn()
        .mockRejectedValueOnce(new Error("isolated chunk transport failure"))
        .mockResolvedValueOnce({ default: { greeting: "Bonjour" } });
      i18n.registerLocaleLoader("fr-FR", loader);
      i18n.setLocale("fr-FR");
      expect(await i18n.loadLocale("fr-FR")).toBe(false);
      expect(i18n.t("greeting")).toBe("Hello");
      expect(warn).toHaveBeenCalledWith("locale_load_failed", expect.objectContaining({ locale: "fr-FR" }));
      expect(await i18n.loadLocale("fr-FR")).toBe(true);
      expect(i18n.t("greeting")).toBe("Bonjour");
    } finally {
      warn.mockRestore();
    }
  });
});
