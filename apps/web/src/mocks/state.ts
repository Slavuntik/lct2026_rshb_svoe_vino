import type { ConsentScope, SensoryVector, SwipeVerdict } from "../lib/apiTypes";
import { findWineBySlug } from "./fixtures/wines";

// Мок-«бэкенд» живёт в памяти вкладки: демо не завязано на реальный сервер, но ведёт себя
// как честное состояние одного пользователя за раз — ровно то, что нужно для сценариев
// онбординга, свайпов и профиля без бэкенда agents/B.

export interface MockAccount {
  token: string;
  kind: "guest" | "registered";
  email?: string;
  birthDate: string;
  consentVersion: string;
  consents: Partial<Record<ConsentScope, boolean>>;
  swipes: Array<{ wine_id: string; verdict: SwipeVerdict }>;
  vector: SensoryVector;
}

const BASELINE_VECTOR: SensoryVector = {
  sweetness: 0.3,
  acidity: 0.5,
  tannin: 0.3,
  body: 0.5,
  oak: 0.2,
  aromatic_intensity: 0.5,
  bubbles: 0.1,
};

const accounts = new Map<string, MockAccount>();
export const waitlistEmails: string[] = [];
export const eventLog: Array<{ name: string; props: unknown; ts: number }> = [];

let counter = 0;

export function createAccount(params: {
  kind: "guest" | "registered";
  birthDate: string;
  consentVersion: string;
  consents?: Partial<Record<ConsentScope, boolean>>;
  email?: string;
}): MockAccount {
  counter += 1;
  const token = `mock-${params.kind}-${counter}-${Math.random().toString(36).slice(2, 8)}`;
  const account: MockAccount = {
    token,
    kind: params.kind,
    email: params.email,
    birthDate: params.birthDate,
    consentVersion: params.consentVersion,
    consents: { base: true, ...params.consents },
    swipes: [],
    vector: { ...BASELINE_VECTOR },
  };
  accounts.set(token, account);
  return account;
}

export function getAccountByToken(token: string | null | undefined): MockAccount | undefined {
  if (!token) return undefined;
  return accounts.get(token);
}

export function upgradeAccountToken(oldToken: string, newAccountParams: { email: string }): MockAccount | undefined {
  const existing = accounts.get(oldToken);
  if (!existing) return undefined;
  accounts.delete(oldToken);
  counter += 1;
  const token = `mock-registered-${counter}-${Math.random().toString(36).slice(2, 8)}`;
  const upgraded: MockAccount = {
    ...existing,
    token,
    kind: "registered",
    email: newAccountParams.email,
    consents: { ...existing.consents, profiling: true },
  };
  accounts.set(token, upgraded);
  return upgraded;
}

export function deleteAccountByToken(token: string): void {
  accounts.delete(token);
}

export function applySwipe(account: MockAccount, wineId: string, verdict: SwipeVerdict): void {
  account.swipes.push({ wine_id: wineId, verdict });
  const wine = findWineBySlug(wineId);
  if (!wine || verdict === "skip") return;
  const sign = verdict === "like" ? 1 : -1;
  const rate = 0.15;
  const axes = Object.keys(account.vector) as (keyof SensoryVector)[];
  for (const axis of axes) {
    const target = wine.derived.sensory[axis];
    const delta = (target - account.vector[axis]) * rate * sign;
    account.vector[axis] = Math.max(0, Math.min(1, account.vector[axis] + delta));
  }
}

export function topStylesFor(account: MockAccount): string[] {
  const counts = new Map<string, number>();
  for (const swipe of account.swipes) {
    if (swipe.verdict !== "like") continue;
    const wine = findWineBySlug(swipe.wine_id);
    for (const style of wine?.derived.reference_style_matches ?? []) {
      counts.set(style, (counts.get(style) ?? 0) + 1);
    }
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([slug]) => slug);
}

/** Только для тестов — сбросить состояние между кейсами. */
export function resetMockState(): void {
  accounts.clear();
  counter = 0;
  waitlistEmails.length = 0;
  eventLog.length = 0;
}
