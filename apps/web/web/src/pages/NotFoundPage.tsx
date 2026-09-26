import { useLocation } from "react-router-dom";
import { WineTile } from "../components/WineTile";
import type { ScanResult } from "../types";

export function NotFoundPage() {
  const scan = (useLocation().state as { scan?: ScanResult } | null)?.scan;
  const similar = scan?.similar ?? [];

  return (
    <section className="results" data-testid="not-found">
      <div className="miss-card">
        <div className="scan-wait-mark">
          <img src="/brand/scanner.svg" alt="" width={119} height={120} />
        </div>
        <p>
          Вино не нашлось.
          <br />
          Но посмотрите аналоги продукта из других виноделен
        </p>
      </div>
      <h2 className="analogs-title">Аналоги</h2>
      <div className="wine-grid">
        {similar.map((wine) => (
          <WineTile key={wine.slug} wine={wine} />
        ))}
      </div>
    </section>
  );
}
