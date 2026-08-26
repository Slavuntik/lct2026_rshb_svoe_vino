import { useI18n } from "../i18n";

/**
 * Тизер функции «Винные дороги» (см. vines/docs/strategy.html, § 3): карта виноделен по
 * регионам, чек-ины, штампы винного паспорта — крючок для инвесторов про туризм (бриф
 * agents/D-landing.md). Функция не реализована — сознательно информационный блок без
 * кнопки и без нового кода аналитики: словарь contracts/events.md заморожен, события
 * под этот блок в нём нет, а придумывать новые имена запрещено протоколом (ORCHESTRATION.md).
 */
export function WineRoadsTeaser() {
  const { t } = useI18n();

  return (
    <section className="card stack" aria-labelledby="wine-roads-heading">
      <span className="badge">{t("landing.wineRoadsBadge")}</span>
      <h2 id="wine-roads-heading">{t("landing.wineRoadsTitle")}</h2>
      <p>{t("landing.wineRoadsText")}</p>
    </section>
  );
}
