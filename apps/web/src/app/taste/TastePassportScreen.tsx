import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { SensoryVectorView } from "../../components/SensoryVectorView";
import { useI18n } from "../../i18n";
import { track } from "../../lib/analytics";
import { apiClient, ApiRequestError } from "../../lib/apiClient";
import type { SwipeVerdict, TasteProfileResponse, WineCardResponse } from "../../lib/apiTypes";
import { storage } from "../../lib/storage";
import { SEED_WINE_IDS } from "./seedWineIds";

type Gate = "checking" | "guest" | "needs-profiling" | "ready";

/** Экран 5/6 — паспорт вкуса: свайпы + текущий вектор в 7 осях (contracts: /taste/*). */
export function TastePassportScreen() {
  const { t } = useI18n();
  const navigate = useNavigate();

  const [gate, setGate] = useState<Gate>("checking");
  const [index, setIndex] = useState(0);
  const [currentWine, setCurrentWine] = useState<WineCardResponse | null>(null);
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
    if (gate !== "ready" || index >= SEED_WINE_IDS.length) return;
    let cancelled = false;
    setCurrentWine(null);
    apiClient
      .getWine(SEED_WINE_IDS[index])
      .then((wine) => {
        if (!cancelled) setCurrentWine(wine);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [gate, index]);

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

      {index < SEED_WINE_IDS.length ? (
        <div
          className="card swipe-card"
          style={{ transform: `translateX(${dragX}px) rotate(${dragX / 20}deg)` }}
          onPointerDown={() => setDragging(true)}
          onPointerMove={(event) => dragging && setDragX(event.movementX ? dragX + event.movementX : dragX)}
          onPointerUp={handlePointerUp}
          onPointerLeave={() => dragging && handlePointerUp()}
        >
          {currentWine ? (
            <div className="stack">
              <img src={currentWine.source.image_url} alt={currentWine.source.name} width={80} />
              <h2>{currentWine.source.name}</h2>
              <p className="text-small">{currentWine.source.winery_name}</p>
              <p>{currentWine.source.description}</p>
            </div>
          ) : (
            <p>{t("common.loading")}</p>
          )}
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
          {profile.top_styles.length > 0 && (
            <>
              <p className="field__label">{t("taste.topStylesTitle")}</p>
              <div className="row">
                {profile.top_styles.map((style) => (
                  <span key={style} className="chip">
                    {style}
                  </span>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
