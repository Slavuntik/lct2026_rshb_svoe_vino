import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { getWine } from "../api";
import type { Wine } from "../types";

const prompts = [
  "Аналог импортного вина",
  "Подбор для новичка",
  "Что из новинок стоит попробовать?",
  "Вино для свидания",
  "Выбери вино за меня",
];

export function WineCardPage() {
  const { slug = "" } = useParams();
  const navigate = useNavigate();
  const [wine, setWine] = useState<Wine | null>(null);
  const [error, setError] = useState("");
  const [question, setQuestion] = useState("");
  const [rating, setRating] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setWine(null);
    setError("");
    getWine(slug)
      .then((item) => {
        if (!cancelled) setWine(item);
      })
      .catch(() => {
        if (!cancelled) setError("Вино не найдено в каталоге.");
      });
    return () => {
      cancelled = true;
    };
  }, [slug]);

  if (error) {
    return (
      <section className="page">
        <p className="error">{error}</p>
        <Link to="/" className="btn">
          Новый скан
        </Link>
      </section>
    );
  }

  if (!wine) {
    return (
      <section className="page">
        <p>Загружаем карточку…</p>
      </section>
    );
  }

  const score = wine.roskachestvoRating == null ? "—" : String(Math.round(wine.roskachestvoRating / 20));
  const region = wine.region.split(".")[0];

  function onAsk(event: FormEvent) {
    event.preventDefault();
    navigate("/sommelier");
  }

  return (
    <section className="wine-detail" data-testid="wine-detail">
      <header className="wine-detail-head">
        <h1>{wine.name}</h1>
        <p>{wine.producer}</p>
        <div className="people-rating">
          <img src="/brand/rating-glass.svg" alt="" width={18} height={29} />
          <span>Народный рейтинг {score}</span>
        </div>
      </header>

      <img className="wine-hero" src={wine.imageUrl} alt="" onError={useBottle} />

      <div className="spec-card">
        <Spec icon="/brand/thumb-region.png" label="Регион" value={region} />
        <Spec icon="/brand/thumb-grape.png" label="Сорт винограда" value={wine.grape} />
        <Spec tone label="Категория и цвет" value={wine.category ?? ""} />
      </div>

      {wine.vineyardImageUrl ? (
        <img className="vineyard" src={wine.vineyardImageUrl} alt="" />
      ) : null}

      <div className="stat-row">
        <article className="stat-card">
          <span className="stat-icon">
            <img src="/brand/icon-percent.svg" alt="" width={24} height={24} />
          </span>
          <p>Температура подачи</p>
          <strong>{wine.servingTemp}°C</strong>
        </article>
        <article className="stat-card">
          <span className="stat-icon">
            <img src="/brand/icon-thermometer.svg" alt="" width={24} height={24} />
          </span>
          <p>Крепость вина</p>
          <strong>{wine.alcohol}%</strong>
        </article>
      </div>

      <p className="tasting">{wine.description}</p>

      <div className="pair-card">
        <div className="pair-lead">
          <span className="stat-icon">
            <img src="/brand/icon-bowl.svg" alt="" width={24} height={24} />
          </span>
          <p>Сочетание с блюдами</p>
        </div>
        <div className="pair-list">
          {(wine.pairings ?? []).map((item) => (
            <article key={item.label} className="pair-item">
              <img src={item.imageUrl} alt="" width={54} height={54} />
              <p>{item.label}</p>
            </article>
          ))}
        </div>
      </div>

      <form className="sommelier-box" onSubmit={onAsk}>
        <h2>Цифровой сомелье</h2>
        <label className="sommelier-field">
          <span>Чем я могу помочь? Задайте вопрос, например: что взять к стейку на гриле?</span>
          <textarea value={question} onChange={(event) => setQuestion(event.target.value)} />
          <div className="chips">
            {prompts.map((prompt) => (
              <button key={prompt} type="button" className="chip" onClick={() => setQuestion(prompt)}>
                {prompt}
              </button>
            ))}
          </div>
        </label>
        <button type="submit" className="send-btn" aria-label="Отправить">
          <img src="/brand/icon-arrow-up.svg" alt="" width={16} height={16} />
        </button>
      </form>

      <div className="rate-card">
        <p>Поставь свою оценку</p>
        <div className="rate-glasses">
          {[1, 2, 3, 4, 5].map((value) => (
            <button key={value} type="button" aria-label={`Оценка ${value}`} onClick={() => setRating(value)}>
              <img
                src={value <= rating ? "/brand/glass-filled.svg" : "/brand/glass-empty.svg"}
                alt=""
                width={48}
                height={48}
              />
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}

function Spec({ icon, tone, label, value }: { icon?: string; tone?: boolean; label: string; value: string }) {
  return (
    <div className="spec-row">
      <span className={tone ? "spec-thumb tone" : "spec-thumb"}>
        {icon ? <img src={icon} alt="" /> : null}
      </span>
      <div>
        <p>{label}</p>
        <strong>{value}</strong>
      </div>
    </div>
  );
}

function useBottle(event: { currentTarget: HTMLImageElement }) {
  const image = event.currentTarget;
  if (!image.src.endsWith("/labels/bottle.png")) {
    image.src = "/labels/bottle.png";
  }
}
