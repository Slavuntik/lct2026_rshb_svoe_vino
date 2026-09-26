import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { NotFoundPage } from "./pages/NotFoundPage";
import { ScannerPage } from "./pages/ScannerPage";
import { SommelierPage } from "./pages/SommelierPage";
import { MatchPage } from "./pages/MatchPage";
import { WineCardPage } from "./pages/WineCardPage";

export function App() {
  const navigate = useNavigate();
  const location = useLocation();

  function openSearch() {
    if (location.pathname !== "/") {
      navigate("/#label-text");
      return;
    }
    document.getElementById("label-text")?.focus();
  }

  return (
    <div className="app">
      <header className="nav">
        <div className="nav-pill">
          <Link to="/" className="nav-logo">
            <img src="/logos/svoe-vino.svg" alt="Своё Вино" width={160} height={40} />
          </Link>
          <div className="nav-actions">
            <button type="button" className="icon-btn" aria-label="Поиск" onClick={openSearch}>
              <img src="/brand/search.svg" alt="" width={24} height={24} />
            </button>
            <Link to="/sommelier" className="menu-btn" aria-label="Меню">
              <img src="/brand/menu.svg" alt="" width={24} height={24} />
            </Link>
          </div>
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<ScannerPage />} />
          <Route path="/match/:slug" element={<MatchPage />} />
          <Route path="/wine/:slug" element={<WineCardPage />} />
          <Route path="/not-found" element={<NotFoundPage />} />
          <Route path="/sommelier" element={<SommelierPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </div>
  );
}
