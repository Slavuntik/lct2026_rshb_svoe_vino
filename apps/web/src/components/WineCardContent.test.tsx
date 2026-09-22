import { screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { WineCardContent } from "./WineCardContent";
import { apiClient } from "../lib/apiClient";
import type { WineCardResponse } from "../lib/apiTypes";
import { findWineBySlug } from "../mocks/fixtures/wines";
import { renderApp } from "../test/renderApp";

function fullCardWithSourceUrl(sourceUrl: string): WineCardResponse {
  const wine = findWineBySlug("tihaya-buhta-chardonnay-reserve-2023");
  if (!wine) throw new Error("fixture-вино не найдено в mocks/fixtures/wines");
  const { searchTerms: _searchTerms, ...card } = wine;
  return { ...card, source_url: sourceUrl };
}

/**
 * v0.4.11 (contracts/image-scan.md): карточка-фолбэк каталога кейса (слага нет в нашем
 * RAG) — ровно {name, winery_name, region_name, grapes, color, category, description,
 * image_url} в source и буквально {} в derived, без остальных полей WineSource/WineDerived
 * (sugar_category/vintage/abv_percent/serving_temp_c/food_pairings/sensory и т.д). apiClient
 * не валидирует payload рантаймом (apiClient.ts: `return payload as T`) — значит именно такой
 * урезанный объект реально долетит до WineCardContent тем же непроверенным приведением типа.
 * Строим его так же (`as unknown as WineCardResponse`), а не полной фикстурой — иначе тест не
 * докажет ничего сверх уже проверенного "полного" случая ниже.
 */
function thinFallbackCard(): WineCardResponse {
  return {
    wine_id: "case-only-shato-yuzhny-sklon-saperavi-2019",
    source: {
      name: "Саперави Резерв",
      winery_name: "Шато Южный Склон",
      region_name: "Южный склон",
      grapes: ["Саперави"],
      color: "красное",
      category: "красное сухое",
      description: "Карточка-фолбэк каталога кейса — слага нет в нашем RAG-каталоге.",
      image_url: "https://vino-svoe.ru/thumbs/shato-yuzhny-sklon-saperavi-2019.webp",
    },
    derived: {},
    source_url: "https://vino-svoe.ru/wines/shato-yuzhny-sklon-saperavi-2019",
    similar: [],
  } as unknown as WineCardResponse;
}

describe("WineCardContent — ссылка «Открыть на «Своё Вино»» (contracts/image-scan.md v0.4.11)", () => {
  it("source_url на vino-svoe.ru — брендированная подпись вместо «Первоисточник»", () => {
    renderApp(
      <WineCardContent wine={fullCardWithSourceUrl("https://vino-svoe.ru/wines/tihaya-buhta-chardonnay-reserve-2023")} />,
    );
    const link = screen.getByRole("link", { name: /открыть на «своё вино»/i });
    expect(link).toHaveAttribute("href", "https://vino-svoe.ru/wines/tihaya-buhta-chardonnay-reserve-2023");
    expect(screen.queryByText(/^первоисточник$/i)).not.toBeInTheDocument();
  });

  it("поддомен vino-svoe.ru — тоже брендированная подпись", () => {
    renderApp(
      <WineCardContent
        wine={fullCardWithSourceUrl("https://api.vino-svoe.ru/wines/tihaya-buhta-chardonnay-reserve-2023")}
      />,
    );
    expect(screen.getByRole("link", { name: /открыть на «своё вино»/i })).toBeInTheDocument();
  });

  it("домен-подделка (vino-svoe.ru.attacker.example) — НЕ брендируется, честный «Первоисточник»", () => {
    renderApp(
      <WineCardContent
        wine={fullCardWithSourceUrl("https://vino-svoe.ru.attacker.example/wines/tihaya-buhta-chardonnay-reserve-2023")}
      />,
    );
    expect(screen.getByRole("link", { name: /первоисточник/i })).toBeInTheDocument();
    expect(screen.queryByText(/открыть на «своё вино»/i)).not.toBeInTheDocument();
  });

  it("прочий домен (example.com — текущие фикстуры) — прежняя подпись «Первоисточник» не сломана", () => {
    renderApp(<WineCardContent wine={fullCardWithSourceUrl("https://example.com/wines/demo")} />);
    expect(screen.getByRole("link", { name: /первоисточник/i })).toBeInTheDocument();
  });

  it("карточка-фолбэк каталога кейса (v0.4.11, урезанный source/derived) — рендерится без падения", () => {
    renderApp(<WineCardContent wine={thinFallbackCard()} />);

    expect(screen.getByText("Саперави Резерв")).toBeInTheDocument();
    // Регион дублируется (подзаголовок "винодельня · регион" + строка dl) — regexp матчит
    // оба, поэтому здесь достаточно проверить подзаголовок целиком, без отдельного запроса
    // на голое "Южный склон" (получил бы "found multiple elements").
    expect(screen.getByText(/Шато Южный Склон · Южный склон/)).toBeInTheDocument();
    expect(screen.getByText("Саперави")).toBeInTheDocument();
    expect(screen.getByText("красное сухое")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Саперави Резерв" })).toBeInTheDocument();

    // Брендированная ссылка — фолбэк каталога кейса тоже source_url на vino-svoe.ru.
    expect(screen.getByRole("link", { name: /открыть на «своё вино»/i })).toBeInTheDocument();

    // Честно отсутствует то, чего действительно нет в урезанном source/derived — не выдумываем
    // "0%"/"0–0°C"/пустой блок сочетаний и не падаем на SensoryVectorView без sensory.
    expect(screen.queryByText(/крепость/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/подача/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/сочетания/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/вкусовой профиль/i)).not.toBeInTheDocument();
  });
});

describe("WineCardContent — regression: карточка каталога (все поля) выглядит как раньше", () => {
  it("полная карточка по-прежнему показывает крепость/подачу/сочетания/вкусовой профиль", () => {
    renderApp(<WineCardContent wine={fullCardWithSourceUrl("https://example.com/wines/demo")} />);

    expect(screen.getByText(/крепость/i)).toBeInTheDocument();
    const servingRow = screen.getByText(/подача/i).closest("div");
    expect(servingRow).not.toBeNull();
    expect(within(servingRow as HTMLElement).getByText(/°C/)).toBeInTheDocument();
    expect(screen.getByText(/сочетания/i)).toBeInTheDocument();
    expect(screen.getByText(/вкусовой профиль/i)).toBeInTheDocument();
  });
});

/**
 * v0.3.3 (contracts/post-scan.md v1.0, задача тимлида 22.09): «К чему подать» —
 * GET /wines/{id}/pairings. Ответ мокается напрямую через vi.spyOn (не через MSW-фикстуру
 * mocks/handlers.ts — та лишь покрывает basis=catalog для dev-режима, см. её комментарий);
 * здесь нужен полный контроль над basis/pairings/message для всех трёх видимых состояний.
 */
describe("WineCardContent — «К чему подать» (contracts/post-scan.md v1.0)", () => {
  function baseWine(): WineCardResponse {
    const wine = findWineBySlug("tihaya-buhta-chardonnay-reserve-2023");
    if (!wine) throw new Error("fixture-вино не найдено в mocks/fixtures/wines");
    const { searchTerms: _searchTerms, ...card } = wine;
    return card;
  }

  it("basis=sensory, pairings непусты — чипы тегов + честная подпись источника", async () => {
    vi.spyOn(apiClient, "getWinePairings").mockResolvedValue({
      wine_id: "tihaya-buhta-chardonnay-reserve-2023",
      basis: "sensory",
      pairings: [
        { tag: "Сыры", score: 0.83, triggered_rules: [{ id: "fat_needs_acidity", explain: "жир сыра просит кислотность" }] },
        { tag: "Блюда из рыбы", score: 0.6, triggered_rules: [] },
      ],
      message: null,
    });

    renderApp(<WineCardContent wine={baseWine()} />);

    const block = await screen.findByTestId("wine-pairings-block");
    expect(within(block).getByText("К чему подать")).toBeInTheDocument();
    expect(await within(block).findByText("Сыры")).toBeInTheDocument();
    expect(within(block).getByText("Блюда из рыбы")).toBeInTheDocument();
    expect(within(block).getByText(/по вкусовому профилю вина/i)).toBeInTheDocument();
  });

  it("pairings=[] — виден message текстом, не пустая тишина", async () => {
    vi.spyOn(apiClient, "getWinePairings").mockResolvedValue({
      wine_id: "tihaya-buhta-chardonnay-reserve-2023",
      basis: "unavailable",
      pairings: [],
      message: "Недостаточно данных, чтобы подобрать сочетания.",
    });

    renderApp(<WineCardContent wine={baseWine()} />);

    const block = await screen.findByTestId("wine-pairings-block");
    expect(await within(block).findByText("Недостаточно данных, чтобы подобрать сочетания.")).toBeInTheDocument();
    expect(within(block).queryByText(/подбираем/i)).not.toBeInTheDocument();
  });

  it("сетевая ошибка — явное состояние, не вечное «Подбираем, к чему подать…»", async () => {
    vi.spyOn(apiClient, "getWinePairings").mockRejectedValue(new Error("network down"));

    renderApp(<WineCardContent wine={baseWine()} />);

    const block = await screen.findByTestId("wine-pairings-block");
    expect(await within(block).findByText(/что-то пошло не так/i)).toBeInTheDocument();
    expect(within(block).queryByText(/подбираем/i)).not.toBeInTheDocument();
  });
});
