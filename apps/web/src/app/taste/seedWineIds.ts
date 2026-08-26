// TODO(contract): в contracts/openapi.yaml v0.2 нет эндпоинта дискавери вин (GET /wines/random
// или аналог) — сцене свайпов физически неоткуда брать очередь кандидатов, кроме единственного
// GET /wines/{wine_id} по уже известному id. Временный сид-список как стаб на месте дыры;
// предложение по контракту — в reports/c-report.md, раздел «Предложения к контрактам».
export const SEED_WINE_IDS = [
  "tihaya-buhta-chardonnay-reserve-2023",
  "severny-sklon-krasnostop-2021",
  "sokoliny-utes-rose-pino-nuar-2024",
  "dom-tihaya-buhta-brut-2022",
  "severny-sklon-riesling-poluslad-2023",
  "sokoliny-utes-merlot-cabernet-2020",
];
