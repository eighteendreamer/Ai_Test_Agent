import { createApp } from "vue";
import { createPinia } from "pinia";

import App from "./App.vue";
import router from "./router";
import { useAppStore } from "./stores/app";
import { useGeneralSettingsStore } from "./stores/generalSettings";
import { registerLocale, registerLocaleLoader, setLocale } from "./services/i18n";
import zhCN from "./locales/zh-CN";
import enUS from "./locales/en-US";
import "katex/dist/katex.min.css";
import "highlight.js/styles/github.css";
import "@vue-flow/core/dist/style.css";
import "@vue-flow/core/dist/theme-default.css";
import "./styles.css";

// Keep the fallback dictionaries immediate; load other dictionaries on selection.
registerLocale("zh-CN", zhCN);
registerLocaleLoader("zh-TW", () => import("./locales/zh-TW"));
registerLocale("en-US", enUS);
registerLocaleLoader("ja-JP", () => import("./locales/ja-JP"));
registerLocaleLoader("ko-KR", () => import("./locales/ko-KR"));
registerLocaleLoader("fr-FR", () => import("./locales/fr-FR"));
registerLocaleLoader("de-DE", () => import("./locales/de-DE"));
registerLocaleLoader("es-ES", () => import("./locales/es-ES"));
registerLocaleLoader("pt-BR", () => import("./locales/pt-BR"));
registerLocaleLoader("ru-RU", () => import("./locales/ru-RU"));
registerLocaleLoader("ar-SA", () => import("./locales/ar-SA"));
registerLocaleLoader("hi-IN", () => import("./locales/hi-IN"));
registerLocaleLoader("id-ID", () => import("./locales/id-ID"));
registerLocaleLoader("vi-VN", () => import("./locales/vi-VN"));
registerLocaleLoader("th-TH", () => import("./locales/th-TH"));

const app = createApp(App);
const pinia = createPinia();
app.use(pinia);
app.use(router);
useAppStore(pinia).hydrateTheme();

// Hydrate general settings and sync i18n locale.
const generalSettingsStore = useGeneralSettingsStore(pinia);
generalSettingsStore.hydrateGeneralSettings().then(() => {
  setLocale(generalSettingsStore.language || "zh-CN");
});

// Watch for language changes and update i18n.
generalSettingsStore.$subscribe((_mutation, state) => {
  if (state.language) {
    setLocale(state.language);
  }
});

app.mount("#app");

// Register Service Worker for desktop notifications.
import("./services/desktopNotifications").then(({ registerServiceWorker }) => {
  registerServiceWorker();
});
