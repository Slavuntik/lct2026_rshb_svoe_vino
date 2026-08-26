import { useEffect } from "react";
import { useI18n } from "../i18n";

interface MetaSelector {
  attr: "name" | "property";
  key: string;
}

/**
 * Ставит/обновляет один <meta>, возвращает функцию отката к исходному состоянию
 * (восстановить прежний content, либо убрать тег целиком, если его не было).
 */
function upsertMeta({ attr, key }: MetaSelector, content: string): () => void {
  const existing = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${key}"]`);
  const previousContent = existing?.getAttribute("content") ?? null;
  const el = existing ?? document.createElement("meta");
  if (!existing) {
    el.setAttribute(attr, key);
    document.head.appendChild(el);
  }
  el.setAttribute("content", content);

  return () => {
    if (existing) {
      if (previousContent !== null) el.setAttribute("content", previousContent);
    } else {
      el.remove();
    }
  };
}

/**
 * SEO-минимум маршрута "/" (agents/D-landing.md): title + description + og:*. Правим DOM
 * напрямую вместо стороннего пакета (react-helmet и т.п.) — apps/web/package.json это
 * конфиг сборки, вне зоны агента D (ORCHESTRATION.md). index.html (зона агента C) уже
 * задаёт честный статический title/description как дефолт для не-JS краулеров и первого
 * пейнта; здесь — только уточнение поверх него на время жизни компонента, с откатом на
 * unmount, чтобы значения не «протекали» на маршрут /app при клиентской навигации.
 */
export function useLandingSeo(): void {
  const { t } = useI18n();
  const title = t("landing.seoTitle");
  const description = t("landing.seoDescription");

  useEffect(() => {
    const previousTitle = document.title;
    document.title = title;

    const restores = [
      upsertMeta({ attr: "name", key: "description" }, description),
      upsertMeta({ attr: "property", key: "og:title" }, title),
      upsertMeta({ attr: "property", key: "og:description" }, description),
      upsertMeta({ attr: "property", key: "og:type" }, "website"),
      upsertMeta({ attr: "property", key: "og:locale" }, "ru_RU"),
      upsertMeta(
        { attr: "property", key: "og:image" },
        new URL("/icons/icon.svg", window.location.origin).toString(),
      ),
    ];

    return () => {
      document.title = previousTitle;
      restores.forEach((restore) => restore());
    };
  }, [title, description]);
}
