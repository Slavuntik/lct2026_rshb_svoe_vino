import type * as ORT from 'onnxruntime-web';
import cvModule from '@techstark/opencv-js';
import type { Box, Catalog, Match, Observation } from './types';

import { selectDetailMatch, plausibleLabelProjection, independentAgreement } from './local-policy';

type CV = typeof cvModule;
type Features = {
  points: Float32Array;
  descriptors: Float32Array;
  width: number;
  height: number;
  count: number;
};
export interface LocalEvidence {
  id: string;
  inliers: number;
  matches: number;
  coverage: number;
}
interface Index {
  version: number;
  ids: string[];
  references: Record<string, string>;
  centers: string;
  vectors: string;
  dimension: number;
  clusters: number;
  points: number;
  extractor: 'aliked' | 'xfeat';
  scoreThreshold: number;
  retrievalDimension?: number;
  verificationReferences?: Record<string, string>;
}
const MATCH_POINTS = 256,
  POINTS = 512,
  CLUSTERS = 32;

/** Catalogue-only assets. No reviewed shelf IDs or crops enter recognition. */
export class LearnedRecognizer {
  private cv!: CV;
  private dimension = 128;
  private vladSize = 4096;
  private retrievalDimension = 128;
  private scoreThreshold = 0.2;
  private centers!: Float32Array;
  private vectors!: Int8Array;
  private norms!: Float32Array;
  private index!: Index;
  private references = new Map<string, Features>();
  private found = new Set<string>();
  private queries = new Map<string, Features>();
  private retrievalQueries = new Map<string, Features>();
  private shortlists = new Map<string, Set<string>>();
  private canvas = new OffscreenCanvas(192, 512);
  private context = this.canvas.getContext('2d', { willReadFrequently: true })!;
  constructor(
    private ort: typeof ORT,
    private extractor: ORT.InferenceSession,
    private matcher: ORT.InferenceSession,
    private asset: (filename: string) => Promise<ArrayBuffer>,
    private cancelled: () => boolean = () => false,
    private retriever?: ORT.InferenceSession,
    private descriptorDot?: ORT.InferenceSession
  ) {}
  async init(indexFile: string, catalog: Catalog) {
    this.cv = cvModule instanceof Promise ? await cvModule : cvModule;
    if (!this.cv.Mat)
      await new Promise<void>((resolve) => {
        this.cv.onRuntimeInitialized = resolve;
      });
    this.index = JSON.parse(new TextDecoder().decode(await this.asset(indexFile)));
    this.dimension = this.index.dimension;
    this.retrievalDimension = this.index.retrievalDimension ?? this.dimension;
    this.vladSize = this.retrievalDimension * CLUSTERS;
    this.scoreThreshold = this.index.extractor === 'xfeat' ? 0 : 0.2;
    if (
      this.index.version !== 1 ||
      ![64, 128].includes(this.dimension) ||
      this.index.clusters !== CLUSTERS ||
      this.index.points !== POINTS ||
      this.index.ids.length !== catalog.wines.length ||
      this.index.ids.some((id, i) => id !== catalog.wines[i].id)
    )
      throw new Error('Индекс деталей не соответствует каталогу.');
    this.centers = new Float32Array(await this.asset(this.index.centers));
    this.vectors = new Int8Array(await this.asset(this.index.vectors));
    if (
      this.centers.length !== this.vladSize ||
      this.vectors.length !== this.index.ids.length * 2 * this.vladSize
    )
      throw new Error('Неверный размер индекса деталей.');
    this.norms = new Float32Array(this.index.ids.length * 2);
    for (let row = 0; row < this.norms.length; row++) {
      let norm = 0;
      for (let j = 0; j < this.vladSize; j++) norm += this.vectors[row * this.vladSize + j] ** 2;
      this.norms[row] = Math.sqrt(norm) || 1;
    }
  }
  resetScene() {
    this.found.clear();
    this.queries.clear();
    this.retrievalQueries.clear();
    this.shortlists.clear();
  }
  private checkCancelled() {
    if (this.cancelled()) throw new DOMException('Обработка отменена', 'AbortError');
  }
  private async extract(
    bitmap: ImageBitmap,
    box: Box,
    session = this.extractor,
    dimension = this.dimension,
    scoreThreshold = this.scoreThreshold
  ): Promise<Features> {
    const [x0, y0, x1, y1] = box;
    const width = Math.max(32, Math.min(512, Math.round(((x1 - x0) * 512) / (y1 - y0))));
    this.canvas.width = Math.ceil(width / 32) * 32;
    this.canvas.height = 512;
    this.context.fillStyle = 'white';
    this.context.fillRect(0, 0, this.canvas.width, 512);
    this.context.drawImage(
      bitmap,
      x0,
      y0,
      x1 - x0,
      y1 - y0,
      Math.floor((this.canvas.width - width) / 2),
      0,
      width,
      512
    );
    const rgba = this.context.getImageData(0, 0, this.canvas.width, 512).data;
    const plane = this.canvas.width * 512,
      input = new Float32Array(plane * 3);
    for (let i = 0; i < plane; i++)
      for (let c = 0; c < 3; c++) input[c * plane + i] = rgba[4 * i + c] / 255;
    const tensor = new this.ort.Tensor('float32', input, [1, 3, 512, this.canvas.width]);
    try {
      const output = await session.run({ image: tensor });
      try {
        const points = output.keypoints.data as Float32Array,
          descriptors = output.descriptors.data as Float32Array,
          scores = output.scores.data as Float32Array;
        const valid = Array.from(scores, (_, i) => i).filter((i) => scores[i] > scoreThreshold);
        const k = new Float32Array(valid.length * 2),
          d = new Float32Array(valid.length * dimension);
        valid.forEach((i, j) => {
          k.set(points.subarray(i * 2, i * 2 + 2), j * 2);
          d.set(descriptors.subarray(i * dimension, (i + 1) * dimension), j * dimension);
        });
        return {
          points: k,
          descriptors: d,
          count: valid.length,
          width: this.canvas.width,
          height: 512
        };
      } finally {
        Object.values(output).forEach((x) => x.dispose());
      }
    } finally {
      tensor.dispose();
    }
  }
  private vlad(f: Features, label: boolean) {
    const output = new Float32Array(this.vladSize);
    for (let i = 0; i < f.count; i++) {
      if (label && f.points[i * 2 + 1] <= f.height * 0.35) continue;
      let best = 0,
        distance = Infinity;
      for (let c = 0; c < CLUSTERS; c++) {
        let d = 0;
        for (let j = 0; j < this.retrievalDimension; j++)
          d +=
            (f.descriptors[i * this.retrievalDimension + j] -
              this.centers[c * this.retrievalDimension + j]) **
            2;
        if (d < distance) {
          distance = d;
          best = c;
        }
      }
      for (let j = 0; j < this.retrievalDimension; j++)
        output[best * this.retrievalDimension + j] +=
          f.descriptors[i * this.retrievalDimension + j] -
          this.centers[best * this.retrievalDimension + j];
    }
    let total = 0;
    for (let c = 0; c < CLUSTERS; c++) {
      let norm = 0;
      for (let j = 0; j < this.retrievalDimension; j++)
        norm += output[c * this.retrievalDimension + j] ** 2;
      norm = Math.sqrt(norm) || 1;
      for (let j = 0; j < this.retrievalDimension; j++) {
        const i = c * this.retrievalDimension + j,
          v = output[i] / norm;
        output[i] = Math.sign(v) * Math.sqrt(Math.abs(v));
        total += output[i] ** 2;
      }
    }
    total = Math.sqrt(total) || 1;
    for (let i = 0; i < this.vladSize; i++) output[i] /= total;
    return output;
  }
  private retrieve(f: Features) {
    const full = this.vlad(f, false),
      label = this.vlad(f, true);
    return this.index.ids
      .map((id, i) => {
        let a = 0,
          b = 0;
        for (let j = 0; j < this.vladSize; j++) {
          a += full[j] * this.vectors[i * 2 * this.vladSize + j];
          b += label[j] * this.vectors[(i * 2 + 1) * this.vladSize + j];
        }
        return { id, score: Math.max(a / this.norms[i * 2], b / this.norms[i * 2 + 1]) };
      })
      .sort((a, b) => b.score - a.score);
  }
  private async reference(id: string, verification = false) {
    const key = verification ? `verification:${id}` : id;
    const expectedDimension = verification ? this.retrievalDimension : this.dimension;
    const cached = this.references.get(key);
    if (cached) {
      this.references.delete(key);
      this.references.set(key, cached);
      return cached;
    }
    const filename = verification
      ? this.index.verificationReferences?.[id]
      : this.index.references[id];
    if (!filename) throw new Error('В индексе отсутствует эталон.');
    const bytes = await this.asset(filename),
      header = new Uint32Array(bytes, 0, 4),
      [count, width, height, dimension] = header;
    if (
      count > POINTS ||
      dimension !== expectedDimension ||
      width < 1 ||
      width > 512 ||
      height !== 512 ||
      bytes.byteLength !== 16 + count * (8 + expectedDimension)
    )
      throw new Error('Повреждены признаки эталона.');
    const points = new Float32Array(bytes.slice(16, 16 + count * 8)),
      quantized = new Int8Array(bytes, 16 + count * 8),
      descriptors = Float32Array.from(quantized, (v) => v / 127);
    for (let i = 0; i < count; i++) {
      let norm = 0;
      for (let j = 0; j < expectedDimension; j++)
        norm += descriptors[i * expectedDimension + j] ** 2;
      norm = Math.sqrt(norm) || 1;
      for (let j = 0; j < expectedDimension; j++) descriptors[i * expectedDimension + j] /= norm;
    }
    const ref = { points, descriptors, count, width, height };
    // ~17 MiB maximum, independent of catalog size. Remaining references stay on disk/HTTP cache.
    if (this.references.size >= 64) this.references.delete(this.references.keys().next().value!);
    this.references.set(key, ref);
    return ref;
  }
  private geometry(
    q: Features,
    r: Features,
    pairs: [number, number][],
    coarse = false
  ): Omit<LocalEvidence, 'id'> {
    const empty = { inliers: 0, matches: pairs.length, coverage: 0 };
    if (pairs.length < 8) return empty;
    const cv = this.cv,
      a = cv.matFromArray(
        pairs.length,
        1,
        cv.CV_32FC2,
        pairs.flatMap(([, j]) => [r.points[j * 2], r.points[j * 2 + 1]])
      );
    const b = cv.matFromArray(
      pairs.length,
      1,
      cv.CV_32FC2,
      pairs.flatMap(([i]) => [q.points[i * 2], q.points[i * 2 + 1]])
    );
    const mask = new cv.Mat();
    let h: InstanceType<CV['Mat']> | undefined;
    try {
      h = cv.findHomography(a, b, coarse ? cv.RHO : cv.RANSAC, 3, mask, coarse ? 512 : 2000, 0.995);
      if (h.empty()) return empty;
      if (!plausibleLabelProjection(h.data64F, [r.width, r.height], [q.width, q.height]))
        return empty;
      const good = pairs.filter((_, i) => mask.data[i]);
      if (good.length < 8) return empty;
      const xs = good.map(([i]) => q.points[i * 2]),
        ys = good.map(([i]) => q.points[i * 2 + 1]);
      const rx = good.map(([, i]) => r.points[i * 2]),
        ry = good.map(([, i]) => r.points[i * 2 + 1]);
      const area = (x: number[], y: number[]) =>
        (Math.max(...x) - Math.min(...x)) * (Math.max(...y) - Math.min(...y));
      const coverage = Math.min(
        area(xs, ys) / (q.width * q.height),
        area(rx, ry) / (r.width * r.height)
      );
      return Number.isFinite(coverage)
        ? { inliers: good.length, matches: pairs.length, coverage }
        : empty;
    } finally {
      a.delete();
      b.delete();
      mask.delete();
      h?.delete();
    }
  }
  private async coarse(q: Features, r: Features, robust = false) {
    if (q.count < 8 || r.count < 8) return { inliers: 0, matches: 0, coverage: 0 };
    const dimension = q.descriptors.length / q.count;
    if (r.descriptors.length / r.count !== dimension)
      throw new Error('Несовместимые локальные признаки.');
    let values: Float32Array;
    if (this.descriptorDot) {
      const query = new this.ort.Tensor('float32', q.descriptors, [q.count, dimension]);
      const reference = new this.ort.Tensor('float32', r.descriptors, [r.count, dimension]);
      try {
        const output = await this.descriptorDot.run({ query, reference });
        try {
          values = Float32Array.from(output.similarities.data as Float32Array);
        } finally {
          Object.values(output).forEach((t) => t.dispose());
        }
      } finally {
        query.dispose();
        reference.dispose();
      }
    } else {
      const cv = this.cv,
        a = cv.matFromArray(q.count, dimension, cv.CV_32F, q.descriptors),
        b = cv.matFromArray(r.count, dimension, cv.CV_32F, r.descriptors);
      const similarities = new cv.Mat(),
        empty = new cv.Mat();
      try {
        cv.gemm(a, b, 1, empty, 0, similarities, cv.GEMM_2_T);
        values = Float32Array.from(similarities.data32F);
      } finally {
        a.delete();
        b.delete();
        similarities.delete();
        empty.delete();
      }
    }
    const rowBest = new Int32Array(q.count),
      columnBest = new Int32Array(r.count);
    const columnScore = new Float32Array(r.count);
    columnScore.fill(-Infinity);
    for (let i = 0; i < q.count; i++) {
      let best = -Infinity,
        index = 0;
      for (let j = 0; j < r.count; j++) {
        const score = values[i * r.count + j];
        if (score > best) {
          best = score;
          index = j;
        }
        if (score > columnScore[j]) {
          columnScore[j] = score;
          columnBest[j] = i;
        }
      }
      rowBest[i] = index;
    }
    const pairs: [number, number][] = [];
    for (let i = 0; i < q.count; i++) {
      const j = rowBest[i];
      if (columnBest[j] === i && values[i * r.count + j] > 0.35) pairs.push([i, j]);
    }
    pairs.sort((a, b) => values[b[0] * r.count + b[1]] - values[a[0] * r.count + a[1]]);
    return this.geometry(q, r, pairs, !robust);
  }

  private async learned(q: Features, r: Features) {
    const k = new Float32Array(2 * MATCH_POINTS * 2),
      d = new Float32Array(2 * MATCH_POINTS * this.dimension);
    [q, r].forEach((f, b) => {
      for (let i = 0; i < Math.min(f.count, MATCH_POINTS); i++) {
        k[(b * MATCH_POINTS + i) * 2] =
          (f.points[i * 2] - f.width / 2) / (Math.max(f.width, f.height) / 2);
        k[(b * MATCH_POINTS + i) * 2 + 1] =
          (f.points[i * 2 + 1] - f.height / 2) / (Math.max(f.width, f.height) / 2);
      }
      d.set(
        f.descriptors.subarray(0, MATCH_POINTS * this.dimension),
        b * MATCH_POINTS * this.dimension
      );
    });
    const keypoints = new this.ort.Tensor('float32', k, [2, MATCH_POINTS, 2]),
      descriptors = new this.ort.Tensor('float32', d, [2, MATCH_POINTS, this.dimension]);
    try {
      const output = await this.matcher.run({ keypoints, descriptors });
      try {
        const values = output.matches.data,
          pairs: [number, number][] = [];
        for (let i = 0; i < values.length; i += 3) {
          const a = Number(values[i + 1]),
            b = Number(values[i + 2]);
          if (a < q.count && b < r.count) pairs.push([a, b]);
        }
        return this.geometry(q, r, pairs);
      } finally {
        Object.values(output).forEach((x) => x.dispose());
      }
    } finally {
      keypoints.dispose();
      descriptors.dispose();
    }
  }
  async recognize(
    bitmap: ImageBitmap,
    box: Box
  ): Promise<{ match: Match; evidence: LocalEvidence[]; verification?: LocalEvidence }> {
    this.checkCancelled();
    const q = await this.extract(bitmap, [
      box[0] * bitmap.width,
      box[1] * bitmap.height,
      box[2] * bitmap.width,
      box[3] * bitmap.height
    ]);
    if (q.count < 16)
      return { match: { id: null, score: 0, margin: 0, candidates: [] }, evidence: [] };
    this.queries.set(box.join(','), q);
    const retrievalFeatures = this.retriever
      ? await this.extract(
          bitmap,
          [
            box[0] * bitmap.width,
            box[1] * bitmap.height,
            box[2] * bitmap.width,
            box[3] * bitmap.height
          ],
          this.retriever,
          this.retrievalDimension,
          0.2
        )
      : q;
    this.retrievalQueries.set(box.join(','), retrievalFeatures);
    const retrieval = this.retrieve(retrievalFeatures),
      ids = new Set([...retrieval.slice(0, 24).map((c) => c.id), ...this.found]);
    this.shortlists.set(box.join(','), new Set(retrieval.slice(0, 100).map((c) => c.id)));
    const coarse: LocalEvidence[] = [];
    // Four bounded concurrent reads avoid paying one network round trip per reference.
    const pendingIds = [...ids];
    for (let start = 0; start < pendingIds.length; start += 4) {
      this.checkCancelled();
      await Promise.all(pendingIds.slice(start, start + 4).map((id) => this.reference(id)));
    }
    for (const id of ids) {
      this.checkCancelled();
      coarse.push({ id, ...(await this.coarse(q, await this.reference(id))) });
    }
    coarse.sort((a, b) => b.inliers - a.inliers);
    // Do not spend transformer inference on crops without a plausible geometric candidate.
    // The scene pass can still retry a catalog ID confirmed on another bottle.
    if (!coarse.length || coarse[0].inliers < 12)
      return {
        match: { id: null, score: 0, margin: 0, candidates: retrieval.slice(0, 3) },
        evidence: []
      };
    // Keep a retrieval candidate as well: a learned matcher can rescue weak raw descriptor matches.
    const finalists = new Set([
      ...coarse.slice(0, 3).map((c) => c.id),
      ...retrieval.slice(0, 2).map((c) => c.id)
    ]);
    const evidence: LocalEvidence[] = [];
    for (const id of finalists) {
      this.checkCancelled();
      evidence.push({ id, ...(await this.learned(q, await this.reference(id))) });
    }
    evidence.sort((a, b) => b.inliers - a.inliers);
    const best = evidence[0],
      second = evidence[1],
      gap = best.inliers - (second?.inliers ?? 0);
    let accepted = selectDetailMatch(evidence);
    const verification = accepted
      ? await this.verifyIndependently(box.join(','), accepted)
      : undefined;
    if (verification && !independentAgreement(verification)) accepted = null;
    if (accepted && this.found.size < 32) this.found.add(accepted);
    // score/margin are geometric inlier ratio/gap ratio, not embedding similarity or probability.
    return {
      match: {
        id: accepted,
        score: best.inliers / Math.max(best.matches, 1),
        margin: gap / Math.max(best.inliers, 1),
        candidates: evidence.map((e) => ({ id: e.id, score: e.inliers / Math.max(e.matches, 1) }))
      },
      evidence,
      verification
    };
  }
  private async verifyIndependently(key: string, id: string): Promise<LocalEvidence | undefined> {
    if (!this.index.verificationReferences) return undefined;
    this.checkCancelled();
    const query = this.retrievalQueries.get(key);
    if (!query) return { id, inliers: 0, matches: 0, coverage: 0 };
    return { id, ...(await this.coarse(query, await this.reference(id, true), true)) };
  }
  /** A second view of the same wine can rescue a weak first view, using only model evidence. */
  async refineScene(observations: Observation[], progress: (done: number, total: number) => void) {
    const pending = observations.filter((o) => !o.match.id && !o.tooSmall);
    for (let i = 0; i < pending.length; i++) {
      this.checkCancelled();
      const observation = pending[i],
        q = this.queries.get(observation.box.join(','));
      if (!q) continue;
      const evidence = [...(observation.geometry ?? [])];
      for (const id of this.found) {
        this.checkCancelled();
        if (evidence.some((e) => e.id === id)) continue;
        const ref = await this.reference(id);
        if (
          !this.shortlists.get(observation.box.join(','))?.has(id) &&
          (await this.coarse(q, ref)).inliers < 8
        )
          continue;
        evidence.push({ id, ...(await this.learned(q, ref)) });
      }
      evidence.sort((a, b) => b.inliers - a.inliers);
      let accepted = selectDetailMatch(evidence, true);
      const verification = accepted
        ? await this.verifyIndependently(observation.box.join(','), accepted)
        : undefined;
      if (verification && !independentAgreement(verification)) accepted = null;
      // Never introduce a new ID through the relaxed repeat threshold.
      if (accepted && this.found.has(accepted)) {
        const best = evidence[0],
          gap = best.inliers - (evidence[1]?.inliers ?? 0);
        observation.match = {
          id: accepted,
          score: best.inliers / Math.max(best.matches, 1),
          margin: gap / Math.max(best.inliers, 1),
          candidates: evidence
            .slice(0, 5)
            .map((e) => ({ id: e.id, score: e.inliers / Math.max(e.matches, 1) }))
        };
        observation.geometry = evidence.slice(0, 5);
        observation.verification = verification;
      }
      progress(i + 1, pending.length);
    }
    this.queries.clear();
    this.retrievalQueries.clear();
    this.shortlists.clear();
  }
}
