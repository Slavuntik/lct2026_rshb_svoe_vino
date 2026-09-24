#!/bin/bash
# bench_vlm.sh <bin-dir> <model.gguf> <mmproj.gguf> <threads> <size> <tag> — llama-mtmd-cli на CPU по кропам img/*_<size>.jpg
set -u
L=/opt/somelye/cpulab; BIN=$1; MODEL=$2; MM=$3; T=$4; SIZE=$5; TAG=$6
P='На фото винная бутылка. Прочитай этикетку и ответь строго одним JSON без пояснений: {"winery": "", "name": "", "grapes": "", "color": "", "sugar": "", "vintage": ""}. Русские надписи — кириллицей, латинские — латиницей. Чего не видно — пустая строка.'
OUT=$L/out/bench_$TAG.log; : > $OUT
export LD_LIBRARY_PATH=$BIN:${LD_LIBRARY_PATH:-}
for img in $L/img/*_${SIZE}.jpg; do
  s=$(date +%s.%N)
  $BIN/llama-mtmd-cli -m $MODEL --mmproj $MM -ngl 0 --no-mmproj-offload -t $T -tb $T --image "$img" -p "$P" -n 120 --temp 0 --no-warmup > $L/out/_o.txt 2> $L/out/_e.txt
  e=$(date +%s.%N)
  wall=$(echo "$e - $s" | bc)
  enc=$(grep -oE "encoding done in [0-9.]+ ms" $L/out/_e.txt | tail -1)
  pe=$(grep -E "prompt eval time" $L/out/_e.txt | tail -1 | sed 's/.*= *//')
  ev=$(grep -E "^.*eval time" $L/out/_e.txt | grep -v prompt | tail -1 | sed 's/.*= *//')
  txt=$(tr '\n' ' ' < $L/out/_o.txt | sed 's/  */ /g' | cut -c1-140)
  echo "$(basename $img) | стенка ${wall}s | ${enc} | prompt: ${pe} | gen: ${ev} | ${txt}" | tee -a $OUT
done
echo BENCH_DONE | tee -a $OUT
