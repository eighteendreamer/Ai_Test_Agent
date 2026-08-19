import { createApp } from "vue";
import { createPinia } from "pinia";

import App from "./App.vue";
import router from "./router";
import { useAppStore } from "./stores/app";
import { useGeneralSettingsStore } from "./stores/generalSettings";
import { registerLocale, setLocale } from "./services/i18n";
import zhCN from "./locales/zh-CN";
import zhTW from "./locales/zh-TW";
import enUS from "./locales/en-US";
import jaJP from "./locales/ja-JP";
import koKR from "./locales/ko-KR";
import frFR from "./locales/fr-FR";
import esES from "./locales/es-ES";
import deDE from "./locales/de-DE";
import ptBR from "./locales/pt-BR";
import ruRU from "./locales/ru-RU";
import arSA from "./locales/ar-SA";
import hiIN from "./locales/hi-IN";
import idID from "./locales/id-ID";
import viVN from "./locales/vi-VN";
import thTH from "./locales/th-TH";
import "katex/dist/katex.min.css";
import "highlight.js/styles/github.css";
import "@vue-flow/core/dist/style.css";
import "@vue-flow/core/dist/theme-default.css";
import "./styles.css";

// Register all locale messages.
registerLocale("zh-CN", zhCN);
registerLocale("zh-TW", zhTW);
registerLocale("en-US", enUS);
registerLocale("ja-JP", jaJP);
registerLocale("ko-KR", koKR);
registerLocale("fr-FR", frFR);
registerLocale("de-DE", deDE);
registerLocale("es-ES", esES);
registerLocale("pt-BR", ptBR);
registerLocale("ru-RU", ruRU);
registerLocale("ar-SA", arSA);
registerLocale("hi-IN", hiIN);
registerLocale("id-ID", idID);
registerLocale("vi-VN", viVN);
registerLocale("th-TH", thTH);

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
