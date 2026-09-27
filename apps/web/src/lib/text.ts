// Разделители, которые могут повиснуть на конце обрезанной строки после того, как мы
// откатились на границу последнего пробела (" · ", " -", " —", запятая и т.п.).
const TRAILING_SEPARATORS = /[\s·•\-–—,;:|]+$/;

/**
 * Обрезает строку по границе слова — не посреди слова — и убирает висячий разделитель на
 * конце. qa-manual-final.md п.5: бейджи цитат в чате резались фиксированной длиной
 * (`quote.slice(0, 40)`) и давали то «· Каберне Совинь» (обрубленное слово), то
 * «Adagum Valley Saperavi · Olymp Winery ·» (висячий «·» без ничего после). Однословный
 * текст длиннее maxLength всё равно нужно как-то сократить — там нет пробела, режем жёстко.
 */
export function truncateAtWordBoundary(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text;

  const hardCut = text.slice(0, maxLength);
  const lastSpace = hardCut.lastIndexOf(" ");
  const wordSafe = lastSpace > 0 ? hardCut.slice(0, lastSpace) : hardCut;
  const cleaned = wordSafe.replace(TRAILING_SEPARATORS, "");

  return cleaned.length > 0 ? `${cleaned}…` : `${hardCut}…`;
}
