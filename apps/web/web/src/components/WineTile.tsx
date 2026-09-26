import type { SyntheticEvent } from "react";
import { Link } from "react-router-dom";
import type { Wine } from "../types";

export function WineTile({ wine, featured = false }: { wine: Wine; featured?: boolean }) {
  const score = wine.roskachestvoRating == null ? "—" : (wine.roskachestvoRating / 20).toFixed(2);

  return (
    <Link to={`/wine/${wine.slug}`} className={featured ? "wine-tile featured" : "wine-tile"}>
      <span className="rating-pill">
        <img src="/brand/rating-glass.svg" alt="" width={18} height={29} />
        <span>{score}</span>
      </span>
      <img className="wine-bottle" src={wine.imageUrl} alt="" width={131} height={131} onError={useBottle} />
      <span className="wine-tile-name">{wine.name}</span>
      <span className="wine-tile-producer">{wine.producer}</span>
    </Link>
  );
}

function useBottle(event: SyntheticEvent<HTMLImageElement>) {
  const image = event.currentTarget;
  if (!image.src.endsWith("/labels/bottle.png")) {
    image.src = "/labels/bottle.png";
  }
}
