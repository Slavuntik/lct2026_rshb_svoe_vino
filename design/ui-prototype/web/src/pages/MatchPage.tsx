import { useEffect, useState } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import { getSimilar, getWine } from "../api";
import { WineTile } from "../components/WineTile";
import type { ScanResult, Wine } from "../types";

export function MatchPage() {
  const { slug = "" } = useParams();
  const scan = (useLocation().state as { scan?: ScanResult } | null)?.scan;
  const [wine, setWine] = useState<Wine | null>(scan?.wine ?? null);
  const [similar, setSimilar] = useState<Wine[]>(scan?.similar ?? []);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    if (!scan?.wine || scan.wine.slug !== slug) {
      getWine(slug)
        .then((item) => {
          if (!cancelled) setWine(item);
        })
        .catch(() => {
          if (!cancelled) setError("Вино не найдено в каталоге.");
        });
    }
    getSimilar(slug).then((items) => {
      if (!cancelled) setSimilar(items.filter((item) => item.slug !== slug));
    });
    return () => {
      cancelled = true;
    };
  }, [slug, scan]);

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

  return (
    <section className="results" data-testid="wine-card">
      <div className="results-hero">
        <WineTile wine={wine} featured />
      </div>
      {similar.length ? (
        <p className="results-more">
          Нашли ещё <strong>{similar.length}</strong> бутылок с похожей этикеткой
        </p>
      ) : null}
      <div className="wine-grid">
        {similar.map((item) => (
          <WineTile key={item.slug} wine={item} />
        ))}
      </div>
    </section>
  );
}
