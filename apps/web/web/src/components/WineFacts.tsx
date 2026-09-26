import type { Wine } from "../types";

const colors: Record<Wine["color"], string> = {
  red: "Красное",
  white: "Белое",
  rose: "Розе",
  orange: "Оранж",
  sparkling: "Игристое",
};

export function WineFacts({ wine }: { wine: Wine }) {
  return (
    <article className="wine" data-testid="wine-card">
      <header>
        <p className="producer">{wine.producer}</p>
        <h1>
          {wine.name} {wine.year}
        </h1>
        <p className="meta">
          {colors[wine.color]} · {wine.region}
        </p>
      </header>
      <p className="lead">{wine.description}</p>
      <dl className="facts">
        <div>
          <dt>Сорт</dt>
          <dd>{wine.grape}</dd>
        </div>
        <div>
          <dt>Роскачество</dt>
          <dd>{wine.roskachestvoRating ?? "—"}</dd>
        </div>
        <div>
          <dt>К чему подать</dt>
          <dd>{wine.pairing}</dd>
        </div>
        <div>
          <dt>Алкоголь / объём</dt>
          <dd>
            {wine.alcohol}% · {wine.volume} мл
          </dd>
        </div>
        {wine.price != null ? (
          <div>
            <dt>Цена</dt>
            <dd>{wine.price} ₽</dd>
          </div>
        ) : null}
      </dl>
    </article>
  );
}
