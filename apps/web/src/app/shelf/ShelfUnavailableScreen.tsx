import { useI18n } from "../../i18n";

/**
 * Честная заглушка вместо iframe: рендерится вместо ShelfScreen, когда живая проверка
 * сервиса витрин (src/lib/shelfAvailability.ts) не подтвердила его доступность — в
 * частности, там, где nginx без локейшна для /shelf-ui иначе показал бы наш же лендинг
 * внутри рамки (reports/architect-post-merge-review.md §3).
 *
 * Задача тимлида 27.09 («Остальные экраны», живой просмотр 375px): у экрана не было класса
 * container — заголовок стоял вплотную к краю (16px, вполовину меньше отступа ~32px на
 * остальных экранах, которые вкладывают screen+container друг в друга) — и голое пустое
 * состояние прямо на фоне страницы, без карточки. Приведено к структуре остальных экранов
 * (header + card, тот же приём, что "Пока вы — гость" в паспорте вкуса) — текст не менялся,
 * только разметка/классы.
 */
export function ShelfUnavailableScreen() {
  const { t } = useI18n();
  return (
    <div className="screen shelf-screen shelf-screen--unavailable container stack">
      <header className="screen__header">
        <h1 className="screen__title">{t("shelf.title")}</h1>
      </header>
      <div className="card stack" data-testid="shelf-unavailable-empty">
        <p>{t("shelf.unavailable")}</p>
      </div>
    </div>
  );
}
