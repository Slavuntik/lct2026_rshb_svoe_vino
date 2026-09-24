"""Materialize explicit visual-review notes; never turn model scores into ground truth."""
import argparse
import collections
import csv
import html
import hashlib
import json
import re
from pathlib import Path

LABELS = {
    'product_match': 'Название и дизайн согласуются; точный SKU/год/крепость не подтверждены',
    'candidate_rejected': 'Первый кандидат отвергнут; товар не идентифицирован',
    'unresolved': 'Недостаточно данных для подтверждения: фрагмент, нечитаемый текст или похожая линейка',
    'wrong_box': 'Рамка не содержит отдельную бутылку',
}
COLORS = {'product_match': '#27a85b', 'candidate_rejected': '#d94747', 'unresolved': '#d99a13', 'wrong_box': '#777777'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args(); root = args.directory
    data = json.loads((root/'results.json').read_text())
    notes = json.loads((root/'visual-review-notes.json').read_text())
    if hashlib.sha256((root/'results.json').read_bytes()).hexdigest() != notes['source_results_sha256']:
        raise ValueError('Results changed: visual notes cannot be applied to different detections')
    if set(notes['photos']) != {str(i) for i in range(1,len(data['rows'])+1)}:
        raise ValueError('Every photograph must have explicit review notes')
    output = root/'visual-review'; output.mkdir(exist_ok=True)
    records, summaries = [], []
    for index, row in enumerate(data['rows'], 1):
        note = notes['photos'][str(index)]; count = len(row['proposals'])
        if note['file'] != row['file'] or note['photo_sha256'] != row['sha256']:
            raise ValueError('Photo identity does not match visual notes')
        if note['default'] != 'mismatch': raise ValueError('Unsupported default')
        claimed = []
        for field in ['consistent', 'uncertain', 'wrong_box']:
            claimed.extend(note.get(field, []))
        claimed.extend(map(int,note.get('corrected',{})))
        if len(set(claimed)) != len(claimed) or any(n<1 or n>count for n in claimed):
            raise ValueError(f'Overlapping or invalid overrides on photo {index}')
        reviewed = []
        for b in row['proposals']:
            n = b['number']; status = 'candidate_rejected'; product = ''
            if n in note.get('uncertain',[]): status = 'unresolved'
            if n in note.get('wrong_box',[]): status = 'wrong_box'
            if n in note.get('consistent',[]):
                status = 'product_match'; product = b['candidates'][0]['id']
            if str(n) in note.get('corrected',{}):
                status = 'product_match'; product = note['corrected'][str(n)]
            matched = next((c for c in b['candidates'] if c['id']==product),None)
            if product and matched is None: raise ValueError('Corrected product absent from reviewed reference set')
            record = {
                'photo':row['file'], 'photo_index':index, 'number':n, 'box':b['box'],
                'review_status':status, 'strong_top1':b['candidates'][0]['id'],
                'strong_top1_verdict': 'consistent_product' if product==b['candidates'][0]['id'] else 'mismatch' if status in ('candidate_rejected','product_match') else 'undetermined',
                'visual_product_id':product or None, 'visual_product_name':matched['name'] if matched else None,
                'reviewed_id':None, 'exact_sku_verified':False, 'vintage_verified':False,
                'compact_accepted':b['browserAcceptedId'],
                'compact_accepted_verdict':'rejected' if n in note.get('compact_rejected',[]) else 'not_accepted' if not b['browserAcceptedId'] else 'undetermined',
                'evidence':note['note'], 'reviewer':'Codex visual inspection',
            }
            records.append(record); reviewed.append(record)
        source = (root/row['file'].replace('.jpg','.html')).read_text()
        svg_match = re.search(r'<svg\b.*?</svg>',source,re.S)
        svg = svg_match.group(); rect = iter(reviewed)
        svg = re.sub(r'<rect\b[^>]*>',lambda m: re.sub(r'stroke="#[^"]+"',f'stroke="{COLORS[next(rect)["review_status"]]}"',m.group()),svg)
        source = source[:svg_match.start()]+svg+source[svg_match.end():]
        for record in reviewed:
            badge = f'<p style="padding:10px;border:3px solid {COLORS[record["review_status"]]}"><b>{LABELS[record["review_status"]]}</b>'
            if record['visual_product_name']:
                badge += '<br>'+html.escape(record['visual_product_name'])+'<br>'+html.escape(record['visual_product_id'])
            if record['compact_accepted_verdict']=='rejected':
                badge += '<br>Ошибка принятия MobileNet: на фото читается TOCORNAL Sauvignon Blanc, не Батрак Мускат.'
            source = source.replace(f'<article id="b{record["number"]}">',f'<article id="b{record["number"]}">{badge}</p>',1)
        legend = '<p><a href="index.html">Все витрины</a> · Проверил Codex по изображениям. Зелёный: совпадает название/дизайн; красный: top-1 отвергнут; жёлтый: не определено; серый: не бутылка. Это не независимая экспертная разметка. Точный SKU, год и крепость не подтверждены. Отклонённый кандидат не означает отсутствие товара в каталоге.</p>'
        source = source.replace('<h1>',legend+'<h1>',1)
        (output/row['file'].replace('.jpg','.html')).write_text(source)
        summaries.append({'photo':row['file'],'regions':count,**dict(collections.Counter(r['review_status'] for r in reviewed))})
    result = {'reviewer':notes['reviewer'], 'scope':'939 detected crops reviewed against top-1 references; enlarged second pass on selected plausible matches; not all 2103 references or top-10 candidates visually exhausted', 'independentGroundTruth':False, 'exactSkuGroundTruth':False, 'records':records, 'summary':summaries}
    (output/'review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    fields = list(records[0])
    with (output/'review.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(records)
    totals = collections.Counter(r['review_status'] for r in records)
    products = {r['visual_product_id'] for r in records if r['visual_product_id']}
    lines = ['# Визуальная проверка всех результатов витрин', '', 'Проверил Codex по изображениям. Это визуальная сверка ИИ, не независимая экспертная разметка. Просмотрены все 939 вырезок на 17 фото рядом с первым кандидатом; правдоподобные совпадения дополнительно увеличены и сопоставлены с исходными эталонами. Все 2103 эталона и все top-10 для каждой вырезки визуально не перебирались.', '', f'- Согласуется название/дизайн: **{totals["product_match"]} областей**, {len(products)} выбранных идентификаторов каталога (не обязательно уникальных физических бутылок).', f'- Первый кандидат визуально отвергнут: **{totals["candidate_rejected"]}**.', f'- Нельзя достоверно решить: **{totals["unresolved"]}**.', f'- Не отдельная бутылка: **{totals["wrong_box"]}**.', '', 'Ни одному результату не присвоен статус точной SKU-разметки: `reviewed_id = null`, `exact_sku_verified = false`. Положительные наблюдения находятся в `visual_product_id`; год, крепость и близкие дубли каталога могут оставаться неразличимыми. Не используйте их как безусловные обучающие метки.', '', 'У AGORA исправлены три первых кандидата на Бастардо по читаемой этикетке и рисунку кипарисов/дорожки. Согласующиеся примеры включают GRAPE DANCE розовое, Шато Тамань Cabernet Sauvignon розовое, AGORA Pinot Noir/Bastardo/Muscat Black, Anima, DESONO Chardonnay, Дыхание волн, Меганом, ROOTSTOCK Glera, Nature Vert и Высокий берег Мюллер-Тургау.', '', 'Единственное принятое компактной моделью совпадение (фото 10, рамка 23) — ложное: TOCORNAL Sauvignon Blanc вместо Батрак Мускат. Это проверка одного принятого ответа на данном наборе, не общая оценка точности. Единственное совпадение top-1 двух моделей (фото 16, рамка 17) тоже визуально неверно: красная узорная этикетка не соответствует Ведерников Фантом.', '', '939 — число рамок, включая фрагменты и повторы, не число бутылок. Отказ/неподтверждение не означает отсутствие SKU на полке. Пропущенные детектором бутылки не размечены. Полную accuracy/recall этого набора по такой проверке вычислять нельзя.', '', '| Фото | Совпадает продукт/дизайн | Top-1 отвергнут | Не определено | Не бутылка |', '|---|---:|---:|---:|---:|']
    for r in summaries:
        lines.append(f'| {r["photo"]} | {r.get("product_match",0)} | {r.get("candidate_rejected",0)} | {r.get("unresolved",0)} | {r.get("wrong_box",0)} |')
    (output/'report.md').write_text('\n'.join(lines)+'\n')
    links = ''.join(f'<li><a href="{r["photo"].replace(".jpg",".html")}">{i+1}: {r["photo"]}</a> — совпадает название/дизайн: {r.get("product_match",0)}, отвергнуто: {r.get("candidate_rejected",0)}, неопределённо: {r.get("unresolved",0)}</li>' for i,r in enumerate(summaries))
    (output/'index.html').write_text('<meta charset="utf-8"><h1>Визуальная проверка витрин</h1><p>Codex просмотрел все 939 вырезок. Проверка ИИ, не независимая экспертная разметка. Зелёные рамки означают совпадение названия/дизайна; точный SKU, год и крепость не подтверждены.</p><p><a href="review.csv">CSV</a> · <a href="review.json">JSON</a> · <a href="report.md">Отчёт</a></p><ul>'+links+'</ul>')
    print(json.dumps({'regions':len(records),'products':len(products),**totals}))

if __name__ == '__main__': main()
