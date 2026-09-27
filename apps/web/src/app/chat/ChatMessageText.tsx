import { Fragment, type ReactNode } from "react";

/**
 * Лёгкий рендер markdown-подмножества ответа сомелье (qa-manual-final.md п.6: LLM-ответ
 * иногда приходит с `**жирным**` и нумерованными списками — до этого компонента текст
 * шёл в интерфейс буквально, со звёздочками разметки на виду, «выглядит недоделанным»).
 * Источник текста — только наш backend (не произвольный пользовательский HTML), поэтому
 * полноценный markdown-парсер избыточен: ровно три конструкции — **жирный**, построчные
 * "1. "/"- " списки, перенос строки внутри абзаца (как и раньше — через CSS
 * white-space:pre-wrap на .chat-bubble, унаследованный сюда; строки внутри абзаца
 * склеиваются "\n", а не <br/>, чтобы не дробить текстовые узлы там, где разметки нет —
 * см. ChatMessageText.test.tsx о том, почему это важно для остальных тестов ChatScreen).
 * Всё, что не распознано (одиночная "*", неизвестные конструкции) — остаётся как есть,
 * без выдумывания форматирования.
 */

const ORDERED_ITEM = /^\d+[.)]\s+(.*)$/;
const UNORDERED_ITEM = /^[-*•]\s+(.*)$/;
const BOLD_SPLIT = /(\*\*[^*\n]+\*\*)/g;

type Block = { kind: "paragraph"; lines: string[] } | { kind: "ordered" | "unordered"; items: string[] };

function toBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  for (const line of text.split("\n")) {
    const ordered = ORDERED_ITEM.exec(line);
    const unordered = !ordered ? UNORDERED_ITEM.exec(line) : null;
    const last = blocks[blocks.length - 1];

    if (ordered) {
      if (last?.kind === "ordered") last.items.push(ordered[1]);
      else blocks.push({ kind: "ordered", items: [ordered[1]] });
    } else if (unordered) {
      if (last?.kind === "unordered") last.items.push(unordered[1]);
      else blocks.push({ kind: "unordered", items: [unordered[1]] });
    } else if (last?.kind === "paragraph") {
      last.lines.push(line);
    } else {
      blocks.push({ kind: "paragraph", lines: [line] });
    }
  }
  return blocks;
}

/** "**жирный**" -> <strong>; одиночные "*" (не в паре) остаются как обычный текст. */
function renderInline(text: string, keyPrefix: string): ReactNode {
  const parts = text.split(BOLD_SPLIT).filter((part) => part !== "");
  if (parts.length === 1) return parts[0];
  return parts.map((part, index) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={`${keyPrefix}-${index}`}>{part.slice(2, -2)}</strong>
    ) : (
      <Fragment key={`${keyPrefix}-${index}`}>{part}</Fragment>
    ),
  );
}

export function ChatMessageText({ text }: { text: string }) {
  const blocks = toBlocks(text);
  return (
    <>
      {blocks.map((block, blockIndex) => {
        switch (block.kind) {
          case "ordered":
          case "unordered": {
            const Tag = block.kind === "ordered" ? "ol" : "ul";
            return (
              <Tag className="chat-text-list" key={blockIndex}>
                {block.items.map((item, itemIndex) => (
                  <li key={itemIndex}>{renderInline(item, `${blockIndex}-${itemIndex}`)}</li>
                ))}
              </Tag>
            );
          }
          case "paragraph":
            return <p key={blockIndex}>{renderInline(block.lines.join("\n"), `${blockIndex}`)}</p>;
        }
      })}
    </>
  );
}
