import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { translate } from "./translate";
import type { DictionaryPath, Locale, Vars } from "./types";

interface I18nContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (path: DictionaryPath, vars?: Vars) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocale] = useState<Locale>("ru");

  const value = useMemo<I18nContextValue>(
    () => ({
      locale,
      setLocale,
      t: (path, vars) => translate(locale, path, vars),
    }),
    [locale],
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) {
    throw new Error("useI18n должен использоваться внутри <I18nProvider>");
  }
  return ctx;
}
