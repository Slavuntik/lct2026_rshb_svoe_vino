import { useI18n } from "../../i18n";

/**
 * Честная заглушка вместо iframe: рендерится вместо ShelfScreen, когда живая проверка
 * сервиса витрин (src/lib/shelfAvailability.ts) не подтвердила его доступность — в
 * частности, там, где nginx без локейшна для /shelf-ui иначе показал бы наш же лендинг
 * внутри рамки (reports/architect-post-merge-review.md §3).
 */
export function ShelfUnavailableScreen() {
  const { t } = useI18n();
  return (
    <section className="screen shelf-screen shelf-screen--unavailable">
      <h1>{t("shelf.title")}</h1>
      <p>{t("shelf.unavailable")}</p>
    </section>
  );
}
