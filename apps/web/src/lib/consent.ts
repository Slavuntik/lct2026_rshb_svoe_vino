import type { ConsentScope } from "./apiTypes";

// Версия текстов согласий — общая для лендинга, онбординга и waitlist. Черновик; см.
// public/legal (зона агента D) для самих текстов после ревью юриста.
export const CONSENT_VERSION = "2026-08-v1";

export const OPTIONAL_CONSENT_SCOPES: ConsentScope[] = ["profiling", "geo", "marketing"];
