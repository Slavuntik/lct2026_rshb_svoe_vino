import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { I18nProvider } from "../i18n";

type InitialRoute = string | { pathname: string; state?: unknown };

/** Общая обёртка для тестов экранов: i18n + роутер, без лишнего. */
export function renderApp(ui: ReactElement, initialRoute: InitialRoute = "/") {
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={[initialRoute]}>{ui}</MemoryRouter>
    </I18nProvider>,
  );
}
