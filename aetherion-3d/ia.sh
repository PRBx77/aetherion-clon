#!/usr/bin/env bash
# Gestor de las IA de texto del X99 (llama.cpp en Docker, solo escuchan en 127.0.0.1).
#
#   ia.sh personal      arranca tu IA personal (uncensored)  → http://127.0.0.1:8082  (chat en el navegador)
#   ia.sh web           arranca la IA oficial de las webs     → http://127.0.0.1:8081  (Aetherion 3D)
#   ia.sh parar         para las dos
#   ia.sh estado        qué está en marcha y uso de las GPUs
#   ia.sh log personal|web
#
# Con las 3 P106 (18 GB) no caben las dos a la vez: al arrancar una se para la otra.
set -euo pipefail

MODELOS=/mnt/nv/ia/llm
IMAGEN=ghcr.io/ggml-org/llama.cpp:server-cuda
GPUS="${AETH_IA_GPUS:-0,1,2}"
CTX="${AETH_IA_CTX:-8192}"

declare -A PUERTO=([personal]=8082 [web]=8081)
declare -A ARCHIVO=([personal]="Qwen3.8-27B-Uncensored-IQ4_XS.gguf" [web]="Qwen3.8-27B-IQ4_XS.gguf")
# Visión (opcional): si el archivo existe se carga en la CPU para no quitar memoria a las GPUs
declare -A VISION=([personal]="mmproj-Qwen3.8-27B-Uncensored-F16.gguf" [web]="mmproj-Qwen3.8-27B-f16.gguf")

parar() { docker rm -f "ia-$1" >/dev/null 2>&1 && echo "ia-$1 parada" || true; }

arrancar() {
  local q=$1 otro
  if curl -fsS "http://127.0.0.1:${PUERTO[$q]}/health" 2>/dev/null | grep -q ok; then
    echo "✅ ia-$q ya estaba en marcha en http://127.0.0.1:${PUERTO[$q]}"; return; fi
  otro=$([[ $q == personal ]] && echo web || echo personal)
  local extra=()
  if [[ -f "$MODELOS/${VISION[$q]}" ]]; then
    extra=(--mmproj "/m/${VISION[$q]}" --no-mmproj-offload); echo "👁  Con visión (${VISION[$q]})"; fi
  local f="$MODELOS/${ARCHIVO[$q]}"
  [[ -f "$f" ]] || { echo "❌ Falta el modelo: $f"; exit 1; }
  parar "$otro"; parar "$q"
  docker run -d --name "ia-$q" --restart no --gpus "\"device=$GPUS\"" \
    -p "127.0.0.1:${PUERTO[$q]}:8080" -v "$MODELOS:/m:ro" "$IMAGEN" \
    -m "/m/${ARCHIVO[$q]}" --alias "aetherion-$q" \
    -ngl 99 --split-mode layer -c "$CTX" -fa on --cache-type-k q8_0 --cache-type-v q8_0 \
    --jinja -t 24 --host 0.0.0.0 --port 8080 "${extra[@]}" >/dev/null
  echo -n "Cargando ia-$q (puede tardar 1–3 min)"
  for _ in $(seq 1 120); do
    if curl -fsS "http://127.0.0.1:${PUERTO[$q]}/health" 2>/dev/null | grep -q ok; then
      echo; echo "✅ ia-$q lista en http://127.0.0.1:${PUERTO[$q]}"; return; fi
    if ! docker ps -q -f name="^ia-$q$" | grep -q .; then
      echo; echo "❌ Se ha parado al cargar. Últimas líneas:"; docker logs --tail 25 "ia-$q" 2>&1; exit 1; fi
    echo -n "."; sleep 3
  done
  echo; echo "⚠️ Sigue cargando; mira: ia.sh log $q"
}

case "${1:-estado}" in
  personal|web) arrancar "$1" ;;
  parar)  parar personal; parar web ;;
  log)    docker logs --tail 60 -f "ia-${2:-personal}" ;;
  estado)
    docker ps --filter name=ia- --format '{{.Names}}  {{.Status}}  {{.Ports}}' | sed 's/^/  /' || true
    nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv,noheader ;;
  *) sed -n '2,10p' "$0" ;;
esac
