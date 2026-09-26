import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { SensoryVectorView } from "../../components/SensoryVectorView";
import { WineImage } from "../../components/WineImage";
import { useI18n } from "../../i18n";
import { track } from "../../lib/analytics";
import { apiClient, ApiRequestError } from "../../lib/apiClient";
import type { SwipeVerdict, TasteCandidateWine, TasteProfileResponse } from "../../lib/apiTypes";
import { storage } from "../../lib/storage";

type Gate = "checking" | "guest" | "needs-profiling" | "ready";

/**
 * Экран 5/6 — паспорт вкуса: свайпы + текущий вектор в 7 осях.
 * Колода — GET /taste/candidates (v0.2.2): сервер сам исключает уже свайпнутые вина,
 * клиент больше не хранит захардкоженный список id (был контрактный пробел — см. c-report.md).
 */
export function TastePassportScreen() {
  const { t } = useI18n();
  const navigate = useNavigate();

  const [gate, setGate] = useState<Gate>("checking");
  const [deck, setDeck] = useState<TasteCandidateWine[] | null>(null);
  const [index, setIndex] = useState(0);
  const [profile, setProfile] = useState<TasteProfileResponse | null>(null);
  const [dragX, setDragX] = useState(0);
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    setGate(storage.getAccountKind() === "guest" ? "guest" : "ready");
  }, []);

  useEffect(() => {
    if (gate !== "ready") return;
    apiClient
      .getTasteProfile()
      .then(setProfile)
      .catch((error: unknown) => {
        if (error instanceof ApiRequestError && error.code === "consent_required") {
          setGate("needs-profiling");
        }
      });
  }, [gate]);

  useEffect(() => {
    if (gate !== "ready") return;
    let cancelled = false;
    apiClient
      .getTasteCandidates()
      .then((response) => {
        if (!cancelled) {
          setDeck(response.wines);
          setIndex(0);
        }
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        if (error instanceof ApiRequestError && error.code === "consent_required") {
          setGate("needs-profiling");
        } else {
          // Честная пустая колода лучше зависшего "Загружаем..." навечно.
          setDeck([]);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [gate]);

  const currentWine = deck && index < deck.length ? deck[index] : null;

  async function handleSwipe(verdict: SwipeVerdict) {
    if (!currentWine) return;
    const wineId = currentWine.wine_id;
    setDragX(0);
    setDragging(false);
    try {
      await apiClient.postSwipe({ wine_id: wineId, verdict });
      track("swipe", { wine_id: wineId, verdict });
      const nextProfile = await apiClient.getTasteProfile();
      setProfile(nextProfile);
      track("taste_profile_updated", { swipes_count: nextProfile.swipes_count });
      setIndex((prev) => prev + 1);
    } catch (error) {
      if (error instanceof ApiRequestError && error.code === "consent_required") {
        setGate("needs-profiling");
      }
    }
  }

  function handlePointerUp() {
    if (!dragging) return;
    const threshold = 80;
    if (dragX > threshold) void handleSwipe("like");
    else if (dragX < -threshold) void handleSwipe("dislike");
    else {
      setDragX(0);
      setDragging(false);
    }
  }

  if (gate === "checking") {
    return <div className="screen container" />;
  }

  if (gate === "guest" || gate === "needs-profiling") {
    return (
      <div className="screen container stack" data-testid="taste-gate">
        <h1 className="screen__title">{t("taste.title")}</h1>
        <div className="card stack">
          <h2>{gate === "guest" ? t("taste.guestTitle") : t("taste.needsProfilingTitle")}</h2>
          <p>{gate === "guest" ? t("taste.guestMessage") : t("taste.needsProfilingMessage")}</p>
          <button type="button" className="btn btn--primary" onClick={() => navigate("/app/profile")}>
            {t("taste.goToProfile")}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="screen container stack">
      <header className="screen__header">
        <h1 className="screen__title">{t("taste.title")}</h1>
        <p className="screen__subtitle">{t("taste.subtitle")}</p>
      </header>

      {deck === null ? (
        <p>{t("common.loading")}</p>
      ) : currentWine ? (
        <div
          className="card swipe-card"
          style={{ transform: `translateX(${dragX}px) rotate(${dragX / 20}deg)` }}
          onPointerDown={() => setDragging(true)}
          onPointerMove={(event) => dragging && setDragX(event.movementX ? dragX + event.movementX : dragX)}
          onPointerUp={handlePointerUp}
          onPointerLeave={() => dragging && handlePointerUp()}
        >
          <div className="stack">
            <WineImage src={currentWine.image_url} alt={currentWine.name} width={80} />
            <h2>{currentWine.name}</h2>
            <p className="text-small">{currentWine.winery_name}</p>
            <div className="row">
              {currentWine.color && <span className="badge">{currentWine.color}</span>}
              {currentWine.region_name && <span className="badge">{currentWine.region_name}</span>}
            </div>
          </div>
          <div className="row row--between">
            <button type="button" className="btn btn--ghost" onClick={() => void handleSwipe("dislike")}>
              {t("taste.dislike")}
            </button>
            <button type="button" className="btn btn--secondary" onClick={() => void handleSwipe("skip")}>
              {t("taste.skip")}
            </button>
            <button type="button" className="btn btn--primary" onClick={() => void handleSwipe("like")}>
              {t("taste.like")}
            </button>
          </div>
        </div>
      ) : (
        <div className="card stack" data-testid="taste-empty">
          <h2>{t("taste.emptyTitle")}</h2>
          <p>{t("taste.emptyMessage")}</p>
        </div>
      )}

      {profile && (
        <div className="card stack">
          <p className="text-small">{t("taste.swipesCount", { count: profile.swipes_count })}</p>
          <h2>{t("taste.vectorTitle")}</h2>
          <SensoryVectorView vector={profile.vector} />
          {/* Задача тимлида 23.09 (аудит architect, openapi 0.3.6): top_styles рендерился голым
              слагом стиля ("chablis") — тот же класс дефекта, что «Похожие вина» (qa-manual
              §5.1). top_styles_named — основной путь (имя + страна); top_styles — запасной,
              когда top_styles_named пуст/отсутствует, с честной порядковой подписью, без
              выдумки имени из слага (тот же приём, что wineCard.similarItemFallback). */}
          {profile.top_styles_named && profile.top_styles_named.length > 0 ? (
            <>
              <p className="field__label">{t("taste.topStylesTitle")}</p>
              <div className="row">
                {profile.top_styles_named.map((style) => (
                  <span key={style.slug} className="chip">
                    {t("taste.topStyleNamed", { name: style.name, country: style.country })}
                  </span>
                ))}
              </div>
            </>
          ) : (
            profile.top_styles.length > 0 && (
              <>
                <p className="field__label">{t("taste.topStylesTitle")}</p>
                <div className="row">
                  {profile.top_styles.map((style, index) => (
                    <span key={style} className="chip">
                      {t("taste.topStyleFallback", { index: index + 1 })}
                    </span>
                  ))}
                </div>
              </>
            )
          )}
        </div>
      )}
    </div>
  );
}
