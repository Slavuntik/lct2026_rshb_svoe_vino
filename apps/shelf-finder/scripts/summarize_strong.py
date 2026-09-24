"""Create a blank human review sheet and paired-model summary from audit_strong results."""
import argparse
import csv
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    result = json.loads((args.directory/'results.json').read_text())
    rows = result['rows']
    fields = ['photo', 'number', 'box', 'strong_top1', 'strong_score', 'geometry_best', 'inliers', 'compact_top1', 'compact_accepted', 'reviewed_id', 'review_status', 'notes']
    # Never overwrite a human review sheet on repeated summaries.
    with (args.directory/'review-template.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader()
        for row in rows:
            for b in row['proposals']:
                writer.writerow(dict(zip(fields, [row['file'], b['number'], json.dumps(b['box']), b['candidates'][0]['id'], b['candidates'][0]['score'], b['geometryBest'], b['maxInliers'], b['browserCandidates'][0]['id'] if b['browserCandidates'] else '', b['browserAcceptedId'] or '', '', 'unreviewed', ''])))
    lines = ['# Сравнение мощной и компактной моделей на витринах', '', f'Каталог: {result["catalogSize"]} SKU. Фото: {len(rows)}. SigLIP2 so400m (бутылка + этикетка, несколько ракурсов), SIFT/MAGSAC для top-10. Полный каталог проверен на совпадение с индексами.', '', 'Детектор YOLO11n остаётся общим: сравнивается распознавание одних и тех же областей. Мощная модель получает исходное разрешение; компактная — вырезки своего штатного прогона. Поэтому это сравнение двух конвейеров, а не чистое сравнение архитектур.', '', '| Фото | Областей | SIFT ≥8 | Совпало top-1 / сравнивалось |', '|---|---:|---:|---:|']
    totals = [0,0,0,0]
    for row in rows:
        boxes = row['proposals']
        counts = [len(boxes),sum(b['maxInliers']>=8 for b in boxes),sum(b['sameTop1'] for b in boxes),sum(bool(b['browserCandidates']) for b in boxes)]
        totals = [a+b for a,b in zip(totals,counts)]
        lines.append(f'| {row["file"]} | {counts[0]} | {counts[1]} | {counts[2]} / {counts[3]} |')
    lines += ['', f'Итого: {totals[0]} областей, {totals[1]} с ≥8 согласованными точками; совпало top-1 у {totals[2]}/{totals[3]}. Это **не accuracy** и не число подтверждённых вин. Порог 8 — диагностический, не калиброванное правило принятия.', '', 'Откройте index.html: страницы содержат фото с номерами, вырезки и эталоны top-3 (также лучший по геометрии, если он вне top-3 и имеет ≥8 точек). Все top-10 сохранены в results.json. Ответы компактной модели взяты из Python/ONNX-прогона, не Safari.', '', 'Для дальнейшей проверки скопируйте review-template.csv в review.csv и заполните reviewed_id только по проверенной этикетке. review_status: confirmed / unknown / unreadable / wrong_box; неизвестный товар не следует принудительно относить к каталогу. Не используйте непроверенные ответы мощной модели как истинную разметку. Не определяйте год или точный SKU только по похожему дизайну.']
    (args.directory/'report.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'photos':len(rows),'regions':totals[0],'geometry8':totals[1],'top1Agreement':totals[2],'compared':totals[3]}))

if __name__ == '__main__': main()
