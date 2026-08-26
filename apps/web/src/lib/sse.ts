import type { ChatStreamEvent } from "./apiTypes";

export interface SseParser {
  /** Скармливаем очередной кусок текста, получаем события, которые уже полностью собрались. */
  push(chunk: string): ChatStreamEvent[];
  /** Забрать то, что осталось в буфере (на случай потока без завершающей пустой строки). */
  flush(): ChatStreamEvent[];
}

function parseEventBlock(block: string): ChatStreamEvent | null {
  const dataLines = block
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice("data:".length).trimStart());
  if (dataLines.length === 0) return null;
  const raw = dataLines.join("\n").trim();
  if (!raw) return null;
  try {
    return JSON.parse(raw) as ChatStreamEvent;
  } catch {
    return null;
  }
}

/**
 * Разбирает text/event-stream из contracts/openapi.yaml (/chat): каждое событие —
 * одна строка `data: {...json...}`, события разделены пустой строкой. Не зависит от
 * fetch/DOM — чистая функция от кусков текста, поэтому легко тестируется на дроблении
 * событий по границам чанков.
 */
export function createSseParser(): SseParser {
  let buffer = "";

  function push(chunk: string): ChatStreamEvent[] {
    buffer += chunk.replace(/\r\n/g, "\n");
    const events: ChatStreamEvent[] = [];
    let sepIndex = buffer.indexOf("\n\n");
    while (sepIndex !== -1) {
      const block = buffer.slice(0, sepIndex);
      buffer = buffer.slice(sepIndex + 2);
      const event = parseEventBlock(block);
      if (event) events.push(event);
      sepIndex = buffer.indexOf("\n\n");
    }
    return events;
  }

  function flush(): ChatStreamEvent[] {
    if (!buffer.trim()) {
      buffer = "";
      return [];
    }
    const event = parseEventBlock(buffer);
    buffer = "";
    return event ? [event] : [];
  }

  return { push, flush };
}

/** Читает fetch Response (text/event-stream) и зовёт onEvent по мере разбора. */
export async function streamChatResponse(
  response: Response,
  onEvent: (event: ChatStreamEvent) => void,
): Promise<void> {
  if (!response.body) {
    // Внутренний инвариант (никогда не показывается пользователю — ChatScreen ловит
    // исключение и рендерит свой честный t("chat.sendError")), поэтому по-английски.
    throw new Error("streamChatResponse: Response has no body");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  const parser = createSseParser();

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    const chunk = decoder.decode(value, { stream: true });
    for (const event of parser.push(chunk)) {
      onEvent(event);
    }
  }

  const tail = decoder.decode();
  const remaining = tail ? parser.push(tail) : [];
  for (const event of [...remaining, ...parser.flush()]) {
    onEvent(event);
  }
}
