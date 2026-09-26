import { en } from "./en";
import { ru } from "./ru";
import type { DictionaryPath, Locale, Vars } from "./types";

const dictionaries: Record<Locale, object> = { ru, en };

function getPath(obj: unknown, path: string): unknown {
  return path.split(".").reduce<unknown>((acc, key) => {
    if (acc && typeof acc === "object" && key in (acc as Record<string, unknown>)) {
      return (acc as Record<string, unknown>)[key];
    }
    return undefined;
  }, obj);
}

function interpolate(template: string, vars?: Vars): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    key in vars ? String(vars[key]) : match,
  );
}

/**
 * Достаёт строку по пути ("onboarding.birthDateLabel") из словаря locale,
 * при отсутствии ключа честно откатывается на ru, а если и там пусто — возвращает
 * сам путь (чтобы дыра в словаре была заметна, а не тихо пропадала).
 */
export function translate(locale: Locale, path: DictionaryPath, vars?: Vars): string {
  const primary = getPath(dictionaries[locale], path);
  const value = typeof primary === "string" ? primary : getPath(ru, path);
  if (typeof value !== "string") {
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.warn(`[i18n] missing dictionary key: ${path}`);
    }
    return path;
  }
  return interpolate(value, vars);
}
