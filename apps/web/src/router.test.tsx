import { fireEvent, screen } from "@testing-library/react";
import { useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { storage } from "./lib/storage";
import { AppRouter } from "./router";
import { renderApp } from "./test/renderApp";

function LocationProbe() {
  return <output data-testid="location">{useLocation().pathname}</output>;
}

function renderRouter(path = "/") {
  return renderApp(<><AppRouter /><LocationProbe /></>, path);
}

describe("Стартовая страница", () => {
  it.each(["/", "/unknown-page", "/app"])("%s открывает сканер после первого входа", async (path) => {
    storage.setOnboardingComplete(true);
    renderRouter(path);
    expect(await screen.findByRole("heading", { name: "Свои вина" }, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/app/scan");
    expect(screen.getByRole("button", { name: "Загрузить фото" })).toBeInTheDocument();
  });

  it("первый вход сохраняет проверку возраста и затем открывает сканер", async () => {
    renderRouter();
    await screen.findByRole("dialog", { name: "18+" });
    expect(screen.getByTestId("location")).toHaveTextContent("/app/onboarding");
    fireEvent.click(screen.getByRole("button", { name: "Подтверждаю" }));
    expect(await screen.findByRole("heading", { name: "Свои вина" }, { timeout: 5000 })).toBeInTheDocument();
    expect(screen.getByTestId("location")).toHaveTextContent("/app/scan");
    expect(storage.isOnboardingComplete()).toBe(true);
  });
});
