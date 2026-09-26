import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useI18n, type DictionaryPath } from "../../i18n";
import { track } from "../../lib/analytics";
import { apiClient, ApiRequestError } from "../../lib/apiClient";
import type { ConsentScope } from "../../lib/apiTypes";
import { CONSENT_VERSION, OPTIONAL_CONSENT_SCOPES } from "../../lib/consent";
import { storage, type AccountKind } from "../../lib/storage";

const SCOPE_LABEL: Record<ConsentScope, DictionaryPath> = {
  base: "profile.scopeBase",
  profiling: "profile.scopeProfiling",
  geo: "profile.scopeGeo",
  marketing: "profile.scopeMarketing",
};

/** Экран 6/6 — «Мои данные»: согласия, апгрейд гостя, экспорт и удаление. */
export function ProfileScreen() {
  const { t } = useI18n();
  const navigate = useNavigate();

  const [accountKind, setAccountKind] = useState<AccountKind | null>(() => storage.getAccountKind());
  const [scopes, setScopes] = useState<Set<ConsentScope>>(() => new Set(storage.getConsentScopes() as ConsentScope[]));

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [upgradeStatus, setUpgradeStatus] = useState<"idle" | "submitting" | "done" | "error">("idle");

  const [exportStatus, setExportStatus] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleteStatus, setDeleteStatus] = useState<"idle" | "loading" | "error">("idle");

  useEffect(() => {
    apiClient
      .getConsents()
      .then((response) => {
        const granted = new Set<ConsentScope>(
          response.consents.filter((c) => c.grant).map((c) => c.scope as ConsentScope),
        );
        granted.add("base");
        setScopes(granted);
      })
      .catch(() => undefined);
  }, []);

  async function toggleScope(scope: ConsentScope, grant: boolean) {
    setScopes((prev) => {
      const next = new Set(prev);
      if (grant) next.add(scope);
      else next.delete(scope);
      storage.setConsentScopes([...next]);
      return next;
    });
    try {
      await apiClient.postConsent({ consent_version: CONSENT_VERSION, grant, scopes: [scope] });
      track(grant ? "consent_granted" : "consent_revoked", { version: CONSENT_VERSION, scope });
    } catch {
      // сеть недоступна — локальное состояние уже обновлено, синхронизируется при следующем заходе
    }
  }

  async function handleUpgrade(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setUpgradeStatus("submitting");
    try {
      const token = await apiClient.register({
        email,
        password,
        birth_date: storage.getBirthDate() ?? "",
        consent_version: CONSENT_VERSION,
        consent_scopes: ["base", ...OPTIONAL_CONSENT_SCOPES.filter((scope) => scopes.has(scope))],
      });
      storage.setAccessToken(token.access_token);
      storage.setAccountKind("registered");
      setAccountKind("registered");
      setUpgradeStatus("done");
    } catch {
      setUpgradeStatus("error");
    }
  }

  async function handleExport() {
    setExportStatus("loading");
    try {
      const data = await apiClient.getDataExport();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "svoy-somelye-data.json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      track("data_export_requested", {});
      setExportStatus("done");
    } catch {
      setExportStatus("error");
    }
  }

  async function handleDelete() {
    setDeleteStatus("loading");
    try {
      await apiClient.deleteProfile();
      track("account_delete_requested", {});
      storage.clearAll();
      navigate("/");
    } catch (error) {
      if (error instanceof ApiRequestError) {
        setDeleteStatus("error");
      }
    }
  }

  return (
    <div className="screen container stack">
      <header className="screen__header">
        <h1 className="screen__title">{t("profile.title")}</h1>
        <p className="screen__subtitle">{t("profile.subtitle")}</p>
      </header>

      <div className="card stack">
        <span className="badge">
          {accountKind === "registered" ? t("profile.accountKindRegistered") : t("profile.accountKindGuest")}
        </span>
        {/* Вне гейта accountKind !== "registered": апгрейд одним и тем же рендером
            переключает и accountKind, и upgradeStatus — если держать сообщение внутри
            формы гостя, оно размонтируется в тот же кадр, где должно появиться, и
            пользователь его никогда не увидит (находка F, e2e). Здесь оно переживает
            переключение бейджа и держится до размонтирования экрана (следующей навигации). */}
        {upgradeStatus === "done" && <p className="badge badge--ok">{t("profile.guestSuccess")}</p>}

        <h2>{t("profile.consentsTitle")}</h2>
        <div className="checkbox-row">
          <input type="checkbox" checked readOnly aria-readonly="true" />
          <span>
            {t(SCOPE_LABEL.base)} <span className="text-caption">({t("profile.scopeBaseLocked")})</span>
          </span>
        </div>
        {OPTIONAL_CONSENT_SCOPES.map((scope) => (
          <label className="checkbox-row" key={scope}>
            <input
              type="checkbox"
              checked={scopes.has(scope)}
              onChange={(event) => void toggleScope(scope, event.target.checked)}
            />
            <span>{t(SCOPE_LABEL[scope])}</span>
          </label>
        ))}
      </div>

      {accountKind !== "registered" && (
        <form className="card stack" onSubmit={handleUpgrade}>
          <h2>{t("profile.guestTitle")}</h2>
          <p className="text-small">{t("profile.guestMessage")}</p>
          <label className="field">
            <span className="field__label">{t("profile.guestEmailLabel")}</span>
            <input
              className="field__input"
              type="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </label>
          <label className="field">
            <span className="field__label">{t("profile.guestPasswordLabel")}</span>
            <input
              className="field__input"
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            <span className="field__hint">{t("profile.guestPasswordHint")}</span>
          </label>
          {/* "done" сюда не долетает — форма скрыта раньше, см. комментарий у бейджа выше. */}
          {upgradeStatus === "error" && <p className="field__error">{t("profile.guestError")}</p>}
          <button type="submit" className="btn btn--primary" disabled={upgradeStatus === "submitting"}>
            {t("profile.guestSubmit")}
          </button>
        </form>
      )}

      <div className="card stack">
        <h2>{t("profile.exportTitle")}</h2>
        <p className="text-small">{t("profile.exportHint")}</p>
        {exportStatus === "done" && <p className="badge badge--ok">{t("profile.exportDone")}</p>}
        {exportStatus === "error" && <p className="field__error">{t("profile.exportError")}</p>}
        <button type="button" className="btn btn--secondary" onClick={() => void handleExport()} disabled={exportStatus === "loading"}>
          {t("profile.exportButton")}
        </button>
      </div>

      <div className="card stack">
        <h2>{t("profile.deleteTitle")}</h2>
        <p className="text-small">{t("profile.deleteWarning")}</p>
        {deleteStatus === "error" && <p className="field__error">{t("profile.deleteError")}</p>}
        <button type="button" className="btn btn--danger" onClick={() => setConfirmingDelete(true)}>
          {t("profile.deleteButton")}
        </button>
      </div>

      {confirmingDelete && (
        <div className="modal-overlay" role="dialog" aria-modal="true">
          <div className="modal stack">
            <h2>{t("profile.deleteConfirmTitle")}</h2>
            <p>{t("profile.deleteConfirmMessage")}</p>
            <div className="row">
              <button type="button" className="btn btn--danger" onClick={() => void handleDelete()} disabled={deleteStatus === "loading"}>
                {t("profile.deleteConfirmButton")}
              </button>
              <button type="button" className="btn btn--ghost" onClick={() => setConfirmingDelete(false)}>
                {t("common.cancel")}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
