import { findWineBySlug } from "./wines";

export interface ChatCitationFixture {
  n: number;
  wine_id: string;
  quote: string;
  url: string;
}

export interface ChatScriptFixture {
  answer: string;
  citations: ChatCitationFixture[];
}

interface ChatRule {
  keywords: string[];
  build: () => ChatScriptFixture;
}

function citationFor(n: number, wineId: string): ChatCitationFixture {
  const wine = findWineBySlug(wineId);
  // «Правило каталога №5» — ответы ведут на портал: url цитаты = source_url вина.
  return { n, wine_id: wineId, quote: wine?.source.description ?? "", url: wine?.source_url ?? "" };
}

// Канонические мок-ответы «сомелье»: каждый факт — с [n], каждый [n] — с citation.
// Никакого обращения к LLM: детерминированные сценарии для демо и тестов.
const rules: ChatRule[] = [
  {
    keywords: ["стейк", "мясо", "гриль", "дичь"],
    build: () => ({
      answer:
        "К стейку хорошо подойдёт «Красностоп Крепкий» [1] — плотные танины выдержат жирность мяса, " +
        "а пряный финиш подчеркнёт специи с гриля. Если хочется мягче, «Мерло-Каберне» [2] с " +
        "бархатистыми танинами тоже не потеряется рядом с мясом.",
      citations: [citationFor(1, "severny-sklon-krasnostop-2021"), citationFor(2, "sokoliny-utes-merlot-cabernet-2020")],
    }),
  },
  {
    keywords: ["рыба", "морепрод", "устриц", "креветк"],
    build: () => ({
      answer:
        "К морепродуктам возьмите «Шардоне Резерв» [1] — маслянистое тело и мягкая кислотность " +
        "обнимут текстуру блюда. Для лёгкой закуски отлично подойдёт «Розе Пино Нуар» [2] с " +
        "освежающей кислотностью.",
      citations: [citationFor(1, "tihaya-buhta-chardonnay-reserve-2023"), citationFor(2, "sokoliny-utes-rose-pino-nuar-2024")],
    }),
  },
  {
    keywords: ["аперитив", "игристое", "праздник", "шампанск"],
    build: () => ({
      answer: "На аперитив — «Брют Резерв» [1]: цитрус и бриошь, тонкие пузырьки не перебьют закуски.",
      citations: [citationFor(1, "dom-tihaya-buhta-brut-2022")],
    }),
  },
  {
    keywords: ["десерт", "сладк", "фрукт"],
    build: () => ({
      answer:
        "К десертам и фруктам берите «Рислинг Полусладкий» [1] — мёд и белый персик со своей " +
        "кислотностью не дадут вкусу стать приторным.",
      citations: [citationFor(1, "severny-sklon-riesling-poluslad-2023")],
    }),
  },
];

/** Возвращает канонический ответ по ключевым словам или null (=> честный refusal). */
export function pickChatResponse(message: string): ChatScriptFixture | null {
  const normalized = message.toLowerCase();
  for (const rule of rules) {
    if (rule.keywords.some((keyword) => normalized.includes(keyword))) {
      return rule.build();
    }
  }
  return null;
}

/**
 * v0.3.5 (задача тимлида 22.09): ChatPayload.wine_id — первый запрос диалога после «Спросить
 * сомелье об этом вине» (ScanScreen.tsx/WineCardScreen.tsx) отвечает про ИМЕННО это вино
 * детерминированно, не гадая по ключевым словам текста (pickChatResponse выше путает
 * вина-близнецы из одной серии — та самая причина фичи, ~8% промахов текстовым поиском).
 * wine_id не резолвится (устаревший/битый слаг) → null, handlers.ts откатывается на
 * pickChatResponse(text) как раньше.
 */
export function pickChatResponseForWine(wineId: string): ChatScriptFixture | null {
  const wine = findWineBySlug(wineId);
  if (!wine) return null;
  return {
    answer: `Конечно — вот что можно рассказать про «${wine.source.name}» от ${wine.source.winery_name} [1]: ${wine.source.description}`,
    citations: [citationFor(1, wineId)],
  };
}

/** Дробим текст на куски по несколько слов — эмулируем токен-стрим SSE. */
export function chunkAnswer(text: string, wordsPerChunk = 3): string[] {
  const words = text.split(" ");
  const chunks: string[] = [];
  for (let i = 0; i < words.length; i += wordsPerChunk) {
    const slice = words.slice(i, i + wordsPerChunk).join(" ");
    chunks.push(i + wordsPerChunk < words.length ? `${slice} ` : slice);
  }
  return chunks;
}
