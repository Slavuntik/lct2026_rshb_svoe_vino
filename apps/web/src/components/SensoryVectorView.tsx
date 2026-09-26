import { useI18n, type DictionaryPath } from "../i18n";
import type { SensoryVector } from "../lib/apiTypes";

const AXES: Array<{ key: keyof SensoryVector; label: DictionaryPath }> = [
  { key: "sweetness", label: "taste.axisSweetness" },
  { key: "acidity", label: "taste.axisAcidity" },
  { key: "tannin", label: "taste.axisTannin" },
  { key: "body", label: "taste.axisBody" },
  { key: "oak", label: "taste.axisOak" },
  { key: "aromatic_intensity", label: "taste.axisAromaticIntensity" },
  { key: "bubbles", label: "taste.axisBubbles" },
];

/** Общий рендер 7-осевого вектора (contracts/openapi.yaml: /taste/profile, /wines/{id}.derived). */
export function SensoryVectorView({ vector }: { vector: SensoryVector }) {
  const { t } = useI18n();

  return (
    <div className="stack stack--tight">
      {AXES.map(({ key, label }) => {
        const percent = Math.round(vector[key] * 100);
        return (
          <div className="taste-axis" key={key}>
            <span className="text-small">{t(label)}</span>
            <span className="taste-axis__track">
              <span className="taste-axis__fill" style={{ width: `${percent}%` }} />
            </span>
            <span className="text-caption text-mono">{percent}%</span>
          </div>
        );
      })}
    </div>
  );
}
