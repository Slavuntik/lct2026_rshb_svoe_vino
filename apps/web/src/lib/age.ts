/** Считает полный возраст на дату now. Общий код для UI-гейта и мок-сервера (contracts). */
export function computeAge(birthDateIso: string, now: Date = new Date()): number | null {
  if (!birthDateIso) return null;
  const birth = new Date(birthDateIso);
  if (Number.isNaN(birth.getTime())) return null;
  let age = now.getFullYear() - birth.getFullYear();
  const monthDiff = now.getMonth() - birth.getMonth();
  if (monthDiff < 0 || (monthDiff === 0 && now.getDate() < birth.getDate())) {
    age -= 1;
  }
  return age;
}

export function isAdult(birthDateIso: string, now?: Date): boolean {
  const age = computeAge(birthDateIso, now);
  return age !== null && age >= 18;
}
