import { Route, Routes } from "react-router-dom";
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { storage } from "../../lib/storage";
import { renderApp } from "../../test/renderApp";
import AppShell from "../AppShell";

describe("Shelf navigation", () => {
  it("keeps the shelf behind onboarding", async () => {
    storage.setOnboardingComplete(false);
    renderApp(<Routes><Route path="/app/*" element={<AppShell />} /></Routes>, "/app/shelf");
    expect(screen.queryByTitle("Поиск вин на витрине")).not.toBeInTheDocument();
  });
  it("embeds the separate shelf app alongside the existing navigation", () => {
    storage.setOnboardingComplete(true);
    renderApp(<Routes><Route path="/app/*" element={<AppShell />} /></Routes>, "/app/shelf");
    expect(screen.getByTitle("Поиск вин на витрине")).toHaveAttribute("src", "/shelf-ui/?engine=server&embedded=1");
    expect(screen.getByRole("link", { name: "Витрина" })).toHaveAttribute("href", "/app/shelf");
    expect(screen.getByRole("link", { name: "Сомелье" })).toHaveAttribute("href", "/app/chat");
    storage.setOnboardingComplete(false);
  });
});
