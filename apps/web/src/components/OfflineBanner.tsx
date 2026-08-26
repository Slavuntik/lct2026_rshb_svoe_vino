import { useI18n } from "../i18n";
import { useOnlineStatus } from "../lib/useOnlineStatus";

/** PWA-требование «не крэшиться без сети»: честный баннер вместо тихого зависания запросов. */
export function OfflineBanner() {
  const online = useOnlineStatus();
  const { t } = useI18n();

  if (online) return null;

  return (
    <div className="offline-banner" role="status">
      {t("offline.message")}
    </div>
  );
}
