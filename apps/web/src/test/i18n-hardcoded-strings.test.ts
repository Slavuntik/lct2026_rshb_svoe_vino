import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// Грубый, но действенный gate (см. agents/C-client.md): интерфейсные строки обязаны идти
// через t() из src/i18n. Исключения осознанные:
//  - src/i18n/**  — сами словари;
//  - src/mocks/** — мок-«бэкенд»: его ответы (в т.ч. error.message) по контракту и так
//    "человеческие, по-русски" (contracts/openapi.yaml) и в реальности придут с сервера
//    agents/B, а не из клиентского i18n;
//  - src/test/**, *.test.ts(x) — тестовый код, не интерфейс.
const SRC_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const EXCLUDED_DIR_NAMES = new Set(["i18n", "mocks", "test"]);
const CYRILLIC = /[Ѐ-ӿ]/;

function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, "") // блочные комментарии
    .replace(/(^|[^:])\/\/.*$/gm, "$1"); // строчные (не трогаем "https://" и т.п.)
}

function collectSourceFiles(dir: string, acc: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (EXCLUDED_DIR_NAMES.has(entry)) continue;
    const fullPath = join(dir, entry);
    const stat = statSync(fullPath);
    if (stat.isDirectory()) {
      collectSourceFiles(fullPath, acc);
    } else if (/\.(ts|tsx)$/.test(entry) && !/\.test\.(ts|tsx)$/.test(entry)) {
      acc.push(fullPath);
    }
  }
  return acc;
}

describe("i18n: в коде нет кириллицы вне словаря (грубый grep-тест по src)", () => {
  it("интерфейсные строки идут через t() — ни одной кириллической строки хардкодом", () => {
    const offenders: string[] = [];
    for (const file of collectSourceFiles(SRC_ROOT)) {
      const code = stripComments(readFileSync(file, "utf8"));
      code.split("\n").forEach((line, index) => {
        if (CYRILLIC.test(line)) {
          offenders.push(`${relative(SRC_ROOT, file)}:${index + 1}: ${line.trim()}`);
        }
      });
    }
    expect(offenders, `Найдена кириллица вне i18n-словаря:\n${offenders.join("\n")}`).toEqual([]);
  });
});
