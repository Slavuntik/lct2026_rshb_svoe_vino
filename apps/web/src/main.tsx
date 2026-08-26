import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { ensureMocksReady } from "./lib/mockBootstrap";
import "./styles/global.css";

// Мок-слой стартует параллельно с рендером, а НЕ до него: первый пейнт лендинга не должен
// платить временем за загрузку MSW-чанка (см. Lighthouse-заметку в reports/c-report.md).
// Корректность не страдает — apiClient.ts дожидается той же ensureMocksReady() перед
// каждым реальным запросом, так что гонка теоретическая (клик быстрее старта воркера).
void ensureMocksReady();

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("#root element not found in index.html");
}
createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
