// Тонкая обёртка над localStorage: без неё нет ни SSR, ни падений в приватном режиме.
// Никаких ПД внутри — только токен, флаги и безымянный вкусовой вектор кешируются локально.

const KEYS = {
  accessToken: "svoy-somelye:access_token",
  accountKind: "svoy-somelye:account_kind",
  onboardingComplete: "svoy-somelye:onboarding_complete",
  consentScopes: "svoy-somelye:consent_scopes",
  birthDate: "svoy-somelye:birth_date",
  landingAgeAck: "svoy-somelye:landing_age_ack",
  locale: "svoy-somelye:locale",
} as const;

export type AccountKind = "guest" | "registered";

function safeGet(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // приватный режим / квота — молча игнорируем, это не критично для работы приложения
  }
}

function safeRemove(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // см. safeSet
  }
}

export const storage = {
  getAccessToken(): string | null {
    return safeGet(KEYS.accessToken);
  },
  setAccessToken(token: string): void {
    safeSet(KEYS.accessToken, token);
  },
  clearAccessToken(): void {
    safeRemove(KEYS.accessToken);
  },

  isOnboardingComplete(): boolean {
    return safeGet(KEYS.onboardingComplete) === "1";
  },
  setOnboardingComplete(value: boolean): void {
    if (value) {
      safeSet(KEYS.onboardingComplete, "1");
    } else {
      safeRemove(KEYS.onboardingComplete);
    }
  },

  /** v0.2: guest (POST /auth/guest) vs registered (POST /auth/register) — влияет на доступ
   * к свайпам/профилю (сервер отдаёт 403 consent_required гостю). */
  getAccountKind(): AccountKind | null {
    const raw = safeGet(KEYS.accountKind);
    return raw === "guest" || raw === "registered" ? raw : null;
  },
  setAccountKind(kind: AccountKind): void {
    safeSet(KEYS.accountKind, kind);
  },

  /** Дата рождения нужна локально, чтобы предложить апгрейд гостя до полного аккаунта
   * (профиль → /auth/register) без повторного вопроса. Это персональные данные пользователя
   * о нём самом — не путать с «никаких ПД в логах/фикстурах» (то — про тестовые данные). */
  getBirthDate(): string | null {
    return safeGet(KEYS.birthDate);
  },
  setBirthDate(value: string): void {
    safeSet(KEYS.birthDate, value);
  },

  getConsentScopes(): string[] {
    const raw = safeGet(KEYS.consentScopes);
    if (!raw) return [];
    try {
      const parsed: unknown = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed.filter((s): s is string => typeof s === "string") : [];
    } catch {
      return [];
    }
  },
  setConsentScopes(scopes: string[]): void {
    safeSet(KEYS.consentScopes, JSON.stringify(scopes));
  },

  hasSeenLandingAgeGate(): boolean {
    return safeGet(KEYS.landingAgeAck) === "1";
  },
  setSeenLandingAgeGate(): void {
    safeSet(KEYS.landingAgeAck, "1");
  },

  /** Полный сброс на удаление аккаунта (профиль → «удалить всё»). */
  clearAll(): void {
    safeRemove(KEYS.accessToken);
    safeRemove(KEYS.accountKind);
    safeRemove(KEYS.onboardingComplete);
    safeRemove(KEYS.consentScopes);
    safeRemove(KEYS.birthDate);
    // landingAgeAck и locale — настройки устройства, а не аккаунта, не трогаем.
  },
};
