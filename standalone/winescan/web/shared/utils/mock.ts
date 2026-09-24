/** Включён ли режим моков: NUXT_PUBLIC_MOCK=1 (после разбора env значение может прийти числом или строкой). */
export function isMockEnabled(value: unknown): boolean {
  return ['1', 'true', 'yes', 'on'].includes(String(value ?? '').trim().toLowerCase())
}
