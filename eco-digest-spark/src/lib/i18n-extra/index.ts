// Source Library / Monitoring / Theme Explorer改修の追加文言を統合する。
import * as sourceLibrary from "./source-library";
import * as monitoring from "./monitoring";
import * as themeExplorer from "./theme-explorer";
import * as sourceSelector from "./source-selector";
import * as hardening from "./hardening";
import * as iosUpload from "./ios-upload";
import * as knowledgeSearch from "./knowledge-search";
import * as externalNetwork from "./external-network";
import * as intelligencePresets from "./intelligence-presets";

export const EXTRA_STRINGS = {
  ja: { ...sourceLibrary.ja, ...monitoring.ja, ...themeExplorer.ja, ...sourceSelector.ja, ...hardening.ja, ...iosUpload.ja, ...knowledgeSearch.ja, ...externalNetwork.ja, ...intelligencePresets.ja },
  en: { ...sourceLibrary.en, ...monitoring.en, ...themeExplorer.en, ...sourceSelector.en, ...hardening.en, ...iosUpload.en, ...knowledgeSearch.en, ...externalNetwork.en, ...intelligencePresets.en },
};
