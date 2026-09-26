import { useI18n } from "../../i18n";

/** Same-origin embedded app keeps shelf models and lifecycle isolated from the main scanner. */
export function ShelfScreen() {
  const { t } = useI18n();
  return (
    <section className="screen shelf-screen">
      <h1>{t("shelf.title")}</h1>
      <p>{t("shelf.description")}</p>
      <a href="/shelf-ui/?engine=server" target="_blank" rel="noopener noreferrer">
        {t("shelf.open")}
      </a>
      <iframe
        className="shelf-frame"
        title={t("shelf.frameTitle")}
        src="/shelf-ui/?engine=server&embedded=1"
        allow="camera; fullscreen"
      />
    </section>
  );
}
