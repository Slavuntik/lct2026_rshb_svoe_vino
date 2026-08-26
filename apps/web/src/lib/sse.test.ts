import { describe, expect, it } from "vitest";
import { createSseParser, streamChatResponse } from "./sse";
import type { ChatStreamEvent } from "./apiTypes";

describe("createSseParser", () => {
  it("собирает событие, пришедшее одним куском", () => {
    const parser = createSseParser();
    const events = parser.push('data: {"type":"token","text":"Привет"}\n\n');
    expect(events).toEqual([{ type: "token", text: "Привет" }]);
  });

    it("собирает событие, раздробленное по границе чанков (включая середину JSON)", () => {
    const parser = createSseParser();
    const full = 'data: {"type":"citation","n":1,"wine_id":"demo"}\n\n';
    const mid = Math.floor(full.length / 2);

    const first = parser.push(full.slice(0, mid));
    expect(first).toEqual([]); // событие ещё не завершено

    const second = parser.push(full.slice(mid));
    expect(second).toEqual([{ type: "citation", n: 1, wine_id: "demo" }]);
  });

  it("отдаёт несколько событий из одного чанка по порядку", () => {
    const parser = createSseParser();
    const events = parser.push(
      'data: {"type":"token","text":"A"}\n\ndata: {"type":"token","text":"B"}\n\ndata: {"type":"done","answer_id":"x"}\n\n',
    );
    expect(events.map((e) => e.type)).toEqual(["token", "token", "done"]);
  });

  it("flush() отдаёт хвост без завершающей пустой строки", () => {
    const parser = createSseParser();
    expect(parser.push('data: {"type":"refusal","reason":"no_results"}')).toEqual([]);
    expect(parser.flush()).toEqual([{ type: "refusal", reason: "no_results" }]);
  });

  it("игнорирует некорректный JSON, не роняя разбор остальных событий", () => {
    const parser = createSseParser();
    const events = parser.push('data: {broken\n\ndata: {"type":"done","answer_id":"x"}\n\n');
    expect(events).toEqual([{ type: "done", answer_id: "x" }]);
  });
});

describe("streamChatResponse", () => {
  it("читает Response с ReadableStream и вызывает onEvent по мере разбора", async () => {
    const chunks = [
      'data: {"type":"citation","n":1,"wine_id":"demo"}\n\n',
      'data: {"type":"token","text":"Здравствуйте"}\n\n',
      'data: {"type":"done","answer_id":"a1"}\n\n',
    ];
    const encoder = new TextEncoder();
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    });
    const response = new Response(stream);

    const received: ChatStreamEvent[] = [];
    await streamChatResponse(response, (event) => received.push(event));

    expect(received).toEqual([
      { type: "citation", n: 1, wine_id: "demo" },
      { type: "token", text: "Здравствуйте" },
      { type: "done", answer_id: "a1" },
    ]);
  });
});
