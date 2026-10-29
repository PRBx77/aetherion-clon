#!/usr/bin/env bash
# Aether: IA pequeña y rápida en la CPU del X99 (no usa GPUs). Puerto 127.0.0.1:8083.
#   ia-asistente.sh            arranca (si no está ya)
#   ia-asistente.sh parar      la para
#   ia-asistente.sh estado     dice si está lista
#   ia-asistente.sh descargar  descarga el modelo
set -u
MODELOS=/mnt/nv/ia/llm
IMAGEN=ghcr.io/ggml-org/llama.cpp:server
REPO="${AETH_ASIST_REPO:-unsloth/Qwen3-4B-Instruct-2507-GGUF}"
PATRON="${AETH_ASIST_PATRON:-*Q5_K_M.gguf}"
HILOS="${AETH_ASIST_HILOS:-20}"
modelo() { ls "$MODELOS"/Qwen3-4B-Instruct-2507*Q5_K_M.gguf 2>/dev/null | head -1; }
lista() { curl -fsS http://127.0.0.1:8083/health 2>/dev/null | grep -q ok; }

case "${1:-arrancar}" in
  descargar)
    docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e HF_HUB_DOWNLOAD_TIMEOUT=60 \
      -v "$MODELOS:/m" clonar:chatterbox hf download "$REPO" --include "$PATRON" --local-dir /m
    m=$(modelo); [[ -n "$m" ]] && echo "✅ Modelo: $m ($(du -h "$m" | cut -f1))" || { echo "❌ No se ha descargado"; exit 1; } ;;
  parar) docker rm -f ia-asistente >/dev/null 2>&1 && echo "ia-asistente parada" ;;
  estado) lista && echo "✅ Aether lista" || echo "Aether apagada o cargando" ;;
  arrancar)
    if lista; then echo "✅ Aether ya estaba lista"; exit 0; fi
    m=$(modelo); [[ -z "$m" ]] && { echo "❌ Falta el modelo: ejecuta  ia-asistente.sh descargar"; exit 1; }
    docker rm -f ia-asistente >/dev/null 2>&1
    docker run -d --name ia-asistente --restart unless-stopped -p 127.0.0.1:8083:8080 \
      -v "$MODELOS:/m:ro" "$IMAGEN" -m "/m/$(basename "$m")" --alias aether \
      -c 16384 -np 2 -t "$HILOS" --jinja --host 0.0.0.0 --port 8080 >/dev/null
    echo -n "Cargando Aether"
    for _ in $(seq 1 60); do lista && { echo; echo "✅ Aether lista en http://127.0.0.1:8083"; exit 0; }; echo -n "."; sleep 2; done
    echo; echo "❌ No arranca. Últimas líneas:"; docker logs --tail 20 ia-asistente 2>&1; exit 1 ;;
  *) echo "Uso: $0 [arrancar|parar|estado|descargar]"; exit 1 ;;
esac
