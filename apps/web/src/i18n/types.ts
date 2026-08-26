import type { RuDictionary } from "./ru";

export type Locale = "ru" | "en";

export type Dictionary = RuDictionary;

export type DeepPartial<T> = {
  [K in keyof T]?: T[K] extends object ? DeepPartial<T[K]> : T[K];
};

/** Строковые пути вида "onboarding.birthDateLabel" — для автодополнения t(). */
export type DictionaryPath<T = Dictionary> = {
  [K in Extract<keyof T, string>]: T[K] extends string ? K : `${K}.${DictionaryPath<T[K]>}`;
}[Extract<keyof T, string>];

export type Vars = Record<string, string | number>;
