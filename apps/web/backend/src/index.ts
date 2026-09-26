import cors from "cors";
import express from "express";
import multer from "multer";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { getWine, similarWines, wines } from "./catalog.js";
import { bestSlug, matchLabel } from "./matcher.js";
import { advise } from "./sommelier.js";
import type { SommelierRequest } from "./types.js";

const root = dirname(fileURLToPath(import.meta.url));
const assetsDir = join(root, "..", "..", "assets");
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 8 * 1024 * 1024 },
});

const app = express();
app.use(cors());
app.use(express.json());
app.use(express.static(assetsDir));

app.get("/api/health", (_req, res) => {
  res.json({ ok: true });
});

app.get("/api/wines", (_req, res) => {
  res.json(wines);
});

app.get("/api/wines/:slug", (req, res) => {
  const wine = getWine(req.params.slug);
  if (!wine) {
    res.status(404).json({ error: "wine_not_found" });
    return;
  }
  res.json(wine);
});

app.get("/api/wines/:slug/similar", (req, res) => {
  res.json(similarWines(req.params.slug, 5));
});

app.post("/api/scan", upload.single("image"), async (req, res) => {
  if (!req.file) {
    res.status(400).json({ error: "image_required" });
    return;
  }
  const result = matchLabel(req.file.originalname, req.file.buffer);
  await wait(MODEL_DELAY_MS);
  res.json(result);
});

app.post("/eval", upload.single("image"), (req, res) => {
  if (!req.file) {
    res.status(400).json({ error: "image_required" });
    return;
  }
  const result = matchLabel(req.file.originalname, req.file.buffer);
  res.json({ slug: bestSlug(result) });
});

app.post("/api/sommelier", (req, res) => {
  const body = req.body as SommelierRequest;
  if (!body?.occasion || !body.dish || !body.sweetness || !body.budget) {
    res.status(400).json({ error: "fields_required" });
    return;
  }
  res.json(advise(body));
});

const MODEL_DELAY_MS = 3000;

function wait(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

const port = Number(process.env.PORT ?? 3001);
app.listen(port, () => {
  console.log(`vinlab api http://localhost:${port}`);
});
