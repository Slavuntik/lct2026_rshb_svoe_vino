import { FormEvent, useState } from "react";
import { Link } from "react-router-dom";
import { askSommelier } from "../api";
import type { SommelierResponse } from "../types";

const fields = [
  {
    name: "occasion",
    label: "К какому случаю",
    options: [
      ["dinner", "Ужин"],
      ["aperitif", "Аперитив"],
      ["gift", "Подарок"],
    ],
  },
  {
    name: "dish",
    label: "Что на столе",
    options: [
      ["steak", "Мясо"],
      ["fish", "Рыба / морепродукты"],
      ["poultry", "Птица"],
      ["cheese", "Сыр"],
      ["picnic", "Лёгкий стол"],
      ["aperitif", "Только бокал"],
    ],
  },
  {
    name: "sweetness",
    label: "Сладость",
    options: [
      ["dry", "Сухое"],
      ["off-dry", "Не принципиально"],
      ["any", "Любое"],
    ],
  },
  {
    name: "budget",
    label: "Бюджет",
    options: [
      ["low", "до 800 ₽"],
      ["mid", "до 1500 ₽"],
      ["high", "без ограничений"],
    ],
  },
] as const;

export function SommelierPage() {
  const [result, setResult] = useState<SommelierResponse | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      setResult(
        await askSommelier({
          occasion: String(data.get("occasion")),
          dish: String(data.get("dish")),
          sweetness: String(data.get("sweetness")),
          budget: String(data.get("budget")),
        }),
      );
    } catch {
      setError("Сомелье сейчас недоступен.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="page">
      <p className="eyebrow">Цифровой сомелье</p>
      <h1>Подберём вино под стол</h1>
      <form className="form" onSubmit={onSubmit}>
        {fields.map((field) => (
          <label key={field.name} className="field">
            <span>{field.label}</span>
            <select name={field.name} required defaultValue={field.options[0][0]}>
              {field.options.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        ))}
        <button type="submit" className="btn primary" disabled={busy}>
          {busy ? "Думаем…" : "Подобрать"}
        </button>
      </form>
      {error ? <p className="error">{error}</p> : null}
      {result ? (
        <div className="muted-block" data-testid="sommelier-result">
          <p>{result.rationale}</p>
          <ul className="card-list">
            {result.wines.map((wine) => (
              <li key={wine.slug}>
                <Link to={`/wine/${wine.slug}`} className="card-link">
                  <strong>
                    {wine.producer} · {wine.name}
                  </strong>
                  <span>
                    {wine.price ? `${wine.price} ₽ · ` : ""}
                    {wine.pairing}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
