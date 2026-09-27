import { useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { WineCardContent } from "../../components/WineCardContent";
import { WineImage } from "../../components/WineImage";
import { useI18n } from "../../i18n";
import { track, type EventPropsMap } from "../../lib/analytics";
import { apiClient } from "../../lib/apiClient";
import type { WineCardResponse } from "../../lib/apiTypes";
import { focusSomelierWidget } from "../../lib/somelierWidget";

type FromSource = EventPropsMap["wine_card_viewed"]["from"];

function isFromSource(value: unknown): value is FromSource {
  return value === "scan" || value === "chat" || value === "similar" || value === "swipe";
}

/** Экран 3/6 — карточка вина: source-поля + derived + ссылка на первоисточник + похожие. */
export function WineCardScreen() {
  const { t } = useI18n();
  const { wineId } = useParams<{ wineId: string }>();
  const location = useLocation();
  const navigate = useNavigate();

  const [wine, setWine] = useState<WineCardResponse | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");

  const locationState = location.state as { from?: unknown } | null;
  const from: FromSource = isFromSource(locationState?.from) ? locationState.from : "scan";

  useEffect(() => {
    if (!wineId) return;
    let cancelled = false;
    setStatus("loading");
    apiClient
      .getWine(wineId)
      .then((data) => {
        if (cancelled) return;
        setWine(data);
        setStatus("ready");
        track("wine_card_viewed", { wine_id: data.wine_id, from });
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });
    return () => {
      cancelled = true;
    };
    // from сознательно не в зависимостях: событие должно уйти один раз на факт открытия карточки,
    // а не при каждой смене "источника перехода" (его и не бывает без смены wineId).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [wineId]);

  function handleSimilarClick(id: string) {
    navigate(`/app/wine/${encodeURIComponent(id)}`, { state: { from: "similar" } });
  }

  /**
   * Задача тимлида 27.09 (макет Figma): виджет сомелье теперь встроен прямо в карточку
   * (WineCardContent → SomelierCardWidget, рендерится выше на этой же странице) — кнопка
   * больше не уводит на отдельный /app/chat, а доскролливает до поля вопроса и ставит туда
   * фокус (lib/somelierWidget.ts). wine_id в первый вопрос виджет передаёт сам, из своего
   * пропа (contracts/openapi.yaml v0.3.5) — навигационный префилл здесь больше не нужен.
   * Раздел «Сомелье» (/app/chat, nav-меню) не тронут и остаётся полностью рабочим.
   */
  function handleAskSomelier() {
    focusSomelierWidget();
  }

  if (status === "loading") {
    return (
      <div className="screen container">
        <p>{t("wineCard.loading")}</p>
      </div>
    );
  }

  if (status === "error" || !wine) {
    return (
      <div className="screen container stack">
        <p>{t("wineCard.notFound")}</p>
        <button type="button" className="btn btn--ghost" onClick={() => navigate("/app/scan")}>
          {t("wineCard.backToScan")}
        </button>
      </div>
    );
  }

  return (
    <div className="screen container stack">
      <button type="button" className="btn btn--ghost btn--sm" onClick={() => navigate("/app/scan")}>
        {t("wineCard.backToScan")}
      </button>

      <WineCardContent wine={wine} titleAs="h1" />

      {/* Задача тимлида 23.09 (backend db090e1/architect openapi 0.3.6, closing qa-manual §5.1):
          similar_wines — обогащённые {wine_id,name,winery,image_url}, тот же порядок, что
          similar; слаг без карточки в каталоге в similar_wines не попадает. similar (голые
          слаги) — DEPRECATED, остаётся только запасным путём, когда similar_wines пуст/нет —
          там честная порядковая подпись без выдумки имени из слага (не title/aria-*: qa читает
          accessibility-дерево тем же тулингом, что нашёл исходный дефект — title в нём
          перекрывает текст узла). Переход по клику не меняется в обоих путях. */}
      {wine.similar_wines && wine.similar_wines.length > 0 ? (
        <div className="stack">
          <h2>{t("wineCard.similarTitle")}</h2>
          <div className="match-list">
            {wine.similar_wines.map((item) => (
              <button
                key={item.wine_id}
                type="button"
                className="match-item"
                onClick={() => handleSimilarClick(item.wine_id)}
              >
                <span className="match-item__main">
                  {item.image_url && (
                    <WineImage src={item.image_url} alt={item.name} width={40} className="match-item__thumb" />
                  )}
                  <span>{item.winery ? `${item.name} · ${item.winery}` : item.name}</span>
                </span>
              </button>
            ))}
          </div>
        </div>
      ) : (
        wine.similar &&
        wine.similar.length > 0 && (
          <div className="stack">
            <h2>{t("wineCard.similarTitle")}</h2>
            <div className="row">
              {wine.similar.map((id, index) => (
                <button key={id} type="button" className="chip" onClick={() => handleSimilarClick(id)}>
                  {t("wineCard.similarItemFallback", { index: index + 1 })}
                </button>
              ))}
            </div>
          </div>
        )
      )}

      <button type="button" className="btn btn--secondary" onClick={handleAskSomelier}>
        {t("wineCard.askSomelier")}
      </button>
    </div>
  );
}
