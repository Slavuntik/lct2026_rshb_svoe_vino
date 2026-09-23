"""Тайминги llama-server (модель загружена постоянно) на кропах этикетки: по фазам из поля timings."""
import base64, glob, json, sys, time, urllib.request
size, fields = sys.argv[1], sys.argv[2]
P_FULL = ('На фото винная бутылка. Прочитай этикетку и ответь строго одним JSON без пояснений: '
          '{"winery": "", "name": "", "grapes": "", "color": "", "sugar": "", "vintage": ""}. '
          'Русские надписи — кириллицей, латинские — латиницей. Чего не видно — пустая строка.')
P_SHORT = ('На фото винная бутылка. Прочитай этикетку и ответь строго одним JSON без пояснений: '
           '{"winery": "", "name": ""}. Русские надписи — кириллицей, латинские — латиницей.')
prompt = P_FULL if fields == "full" else P_SHORT
rows = []
for img in sorted(glob.glob(f"/opt/somelye/cpulab/img/*_{size}.jpg")):
    b64 = base64.b64encode(open(img, "rb").read()).decode()
    body = {"model": "x", "temperature": 0, "max_tokens": 120 if fields == "full" else 40,
            "messages": [{"role": "user", "content": [{"type": "text", "text": prompt},
                         {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}}]}]}
    t = time.time()
    r = urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8099/v1/chat/completions",
        data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}), timeout=600)
    d = json.loads(r.read()); wall = time.time() - t
    tm = d.get("timings", {}); u = d.get("usage", {})
    rows.append((wall, tm.get("prompt_ms", 0) / 1000, tm.get("predicted_ms", 0) / 1000, u.get("prompt_tokens"), u.get("completion_tokens")))
    txt = d["choices"][0]["message"]["content"].replace("\n", " ")[:90]
    print(f"{img.split('/')[-1]:16s} всего {wall:5.1f} с | картинка+промпт {rows[-1][1]:5.1f} с ({rows[-1][3]} ток.) | генерация {rows[-1][2]:5.1f} с ({rows[-1][4]} ток.) | {txt}", flush=True)
import statistics as st
rows = rows[1:] if len(rows) > 2 else rows  # первый запрос — прогрев
print(f"МЕДИАНА {size}px/{fields}: всего {st.median(r[0] for r in rows):.1f} с = промпт {st.median(r[1] for r in rows):.1f} с + генерация {st.median(r[2] for r in rows):.1f} с; "
      f"скорость генерации {st.median(r[4] / r[2] for r in rows if r[2]):.1f} ток/с, промпта {st.median(r[3] / r[1] for r in rows if r[1]):.0f} ток/с")
