import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { WineImage } from "../../components/WineImage";
import { useI18n } from "../../i18n";
import { apiClient } from "../../lib/apiClient";
import type { CatalogItem } from "../../lib/apiTypes";

const PAGE_SIZE = 24;
// Дебаунс поиска/фильтров — не бьём в /v1/catalog на каждое нажатие клавиши; 2103 позиции на
// боевом каталоге отвечают за миллисекунды (reports/backend-catalog-list.md), но сеть и
// пользовательский ввод — нет. Значение подобрано по ощущению (не измерено на живом стенде).
const SEARCH_DEBOUNCE_MS = 300;

/**
 * Экран «Каталог вин» (задача тимлида 27.09, макет Figma — там это домашний экран; у нас
 * входная точка остаётся сканером, поэтому раздел добавлен только в меню, см.
 * reports/frontend-catalog-screen.md «Домашний экран или нет»).
 *
 * GET /v1/catalog (contracts/openapi.yaml v0.3.7): постранично, лимит по умолчанию 24 — экран
 * никогда не грузит все 2103 позиции разом. Пагинация — кнопка «Показать ещё», не бесконечная
 * прокрутка: обычная фокусируемая кнопка проще для клавиатуры и скринридера, не требует
 * IntersectionObserver и не даёт неожиданных скачков контента (макет допускал оба варианта).
 *
 * Заглушка без превью — переиспользован WineImage (components/WineImage.tsx): она уже рисует
 * нейтральный силуэт бутылки на токенах при пустом/битом src, ничего нового изобретать не
 * пришлось; `image_url: null` (v0.3.7, 49 из 2103 позиций без файла) передаётся туда как есть.
 */
export function CatalogScreen() {
  const { t } = useI18n();
  const navigate = useNavigate();

  const [q, setQ] = useState("");
  const [color, setColor] = useState("");
  const [sugar, setSugar] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);

  const [items, setItems] = useState<CatalogItem[] | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(false);
  const [loadMoreBusy, setLoadMoreBusy] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState(false);
  // Нужен внутри debounce-эффекта, чтобы дёрнуть загрузку заново кнопкой "Повторить" без
  // дублирования query-логики — обычный "тик", не часть публичного состояния экрана.
  const [retryTick, setRetryTick] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setRefreshing(true);
    const timer = setTimeout(() => {
      apiClient
        .getCatalog({ limit: PAGE_SIZE, offset: 0, q, color, sugar })
        .then((response) => {
          if (cancelled) return;
          setItems(response.wines);
          setTotal(response.total);
          setError(false);
          setRefreshing(false);
        })
        .catch(() => {
          if (cancelled) return;
          setError(true);
          setRefreshing(false);
        });
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- retryTick меняется только для повторного запуска этого же эффекта
  }, [q, color, sugar, retryTick]);

  async function handleLoadMore() {
    if (!items) return;
    setLoadMoreBusy(true);
    setLoadMoreError(false);
    try {
      const response = await apiClient.getCatalog({ limit: PAGE_SIZE, offset: items.length, q, color, sugar });
      setItems((prev) => [...(prev ?? []), ...response.wines]);
      setTotal(response.total);
    } catch {
      setLoadMoreError(true);
    } finally {
      setLoadMoreBusy(false);
    }
  }

  function handleSearchSubmit(event: FormEvent<HTMLFormElement>) {
    // Поле и так живёт по debounce (onChange ниже) — submit только на случай Enter/мобильной
    // клавиатуры с "Найти": не даём странице перезагрузиться формой без onSubmit.
    event.preventDefault();
  }

  function handleRetry() {
    setError(false);
    setRetryTick((tick) => tick + 1);
  }

  const hasMore = total !== null && items !== null && items.length < total;

  return (
    <div className="screen container stack">
      <header className="screen__header">
        <h1 className="screen__title">{t("catalog.title")}</h1>
        <p className="screen__subtitle">{t("catalog.subtitle")}</p>
      </header>

      <form className="stack stack--tight" onSubmit={handleSearchSubmit}>
        <label className="field">
          <span className="visually-hidden">{t("catalog.searchLabel")}</span>
          <input
            className="field__input"
            type="search"
            value={q}
            onChange={(event) => setQ(event.target.value)}
            placeholder={t("catalog.searchPlaceholder")}
          />
        </label>

        <div className="row">
          <button
            type="button"
            className="chip"
            aria-pressed={filtersOpen}
            onClick={() => setFiltersOpen((open) => !open)}
          >
            {t("catalog.filtersToggle")}
          </button>
          {total !== null && !error && <span className="text-caption">{t("catalog.resultsCount", { count: total })}</span>}
        </div>

        {filtersOpen && (
          <div className="row card">
            <label className="field">
              <span className="field__label">{t("catalog.filterColor")}</span>
              <input
                className="field__input"
                value={color}
                onChange={(event) => setColor(event.target.value)}
                placeholder={t("catalog.filterColorPlaceholder")}
              />
            </label>
            <label className="field">
              <span className="field__label">{t("catalog.filterSugar")}</span>
              <input
                className="field__input"
                value={sugar}
                onChange={(event) => setSugar(event.target.value)}
                placeholder={t("catalog.filterSugarPlaceholder")}
              />
            </label>
          </div>
        )}
      </form>

      {items === null && !error && <p className="text-small">{t("catalog.loading")}</p>}
      {error && (
        <div className="stack stack--tight">
          <p className="field__error">{t("catalog.error")}</p>
          <button type="button" className="btn btn--ghost btn--sm" onClick={handleRetry}>
            {t("common.retry")}
          </button>
        </div>
      )}

      {items !== null && !error && (
        <>
          {refreshing && <p className="text-caption">{t("catalog.searching")}</p>}

          {items.length === 0 ? (
            <p className="text-small">{t("catalog.empty")}</p>
          ) : (
            <div className="catalog-grid" data-testid="catalog-grid">
              {items.map((item) => (
                <button
                  key={item.wine_id}
                  type="button"
                  className="catalog-tile"
                  onClick={() => navigate(`/app/wine/${encodeURIComponent(item.wine_id)}`)}
                >
                  <WineImage src={item.image_url ?? undefined} alt={item.name} width={110} className="catalog-tile__image" />
                  <span className="catalog-tile__name">{item.name}</span>
                  {item.winery && <span className="catalog-tile__winery">{item.winery}</span>}
                </button>
              ))}
            </div>
          )}

          {hasMore && (
            <div className="stack stack--tight" style={{ alignItems: "center" }}>
              <button type="button" className="btn btn--secondary" onClick={handleLoadMore} disabled={loadMoreBusy}>
                {loadMoreBusy ? t("catalog.loadingMore") : t("catalog.loadMore")}
              </button>
              {loadMoreError && <p className="field__error">{t("catalog.loadMoreError")}</p>}
            </div>
          )}
        </>
      )}
    </div>
  );
}
