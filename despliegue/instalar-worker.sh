#!/usr/bin/env bash
# Instala el worker de Aetherion Diseño como servicio en el X99.
#
#   sudo bash instalar-worker.sh https://tu-dominio.com CLAVE_DEL_WORKER
#
# Solo hace conexiones SALIENTES a la web. Arranca solo al encender y se reinicia si falla.
set -euo pipefail

WEB="${1:-}"; CLAVE="${2:-}"
[[ -z "$WEB" || -z "$CLAVE" ]] && { echo "Uso: sudo bash $0 https://tu-dominio.com CLAVE"; exit 1; }
[[ $EUID -ne 0 ]] && { echo "Ejecuta con sudo"; exit 1; }
[[ "$WEB" == https://* ]] || { echo "La dirección debe empezar por https://"; exit 1; }
USUARIO="${SUDO_USER:-xxx}"
ORIGEN="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Comprobando que la web responde…"
curl -fsS -o /dev/null "$WEB/" || { echo "❌ No llego a $WEB"; exit 1; }
codigo=$(curl -s -o /dev/null -w "%{http_code}" -H "Authorization: Bearer $CLAVE" "$WEB/worker/ping")
case "$codigo" in
  200) echo "✅ Web y clave correctas" ;;
  401) echo "❌ La web rechaza la clave del worker"; exit 1 ;;
  *) echo "❌ Respuesta inesperada de la web: $codigo"; exit 1 ;;
esac

echo "==> Instalando el worker…"
install -d /opt/aetherion-worker
install -m 755 "$ORIGEN/worker/worker.py" /opt/aetherion-worker/worker.py
[[ -f "$ORIGEN/worker/avatar.py" ]] && install -m 755 "$ORIGEN/worker/avatar.py" /mnt/nv/ia/avatar.py
if [[ -d "$ORIGEN/aetherion-3d" ]]; then
  echo "==> Instalando el motor de Aetherion 3D…"
  install -d -o "$USUARIO" -g "$USUARIO" /mnt/nv/ia/aetherion-3d
  install -m 755 -o "$USUARIO" -g "$USUARIO" "$ORIGEN/aetherion-3d/modelar3d.py" /mnt/nv/ia/aetherion-3d/modelar3d.py
  cp -r "$ORIGEN/aetherion-3d/sandbox" /mnt/nv/ia/aetherion-3d/ && chown -R "$USUARIO:$USUARIO" /mnt/nv/ia/aetherion-3d/sandbox
  install -m 755 "$ORIGEN/aetherion-3d/ia.sh" /usr/local/bin/ia.sh
  [[ -f "$ORIGEN/aetherion-3d/aetherion-3d" ]] && install -m 755 "$ORIGEN/aetherion-3d/aetherion-3d" /usr/local/bin/aetherion-3d
  docker image inspect aetherion-openscad >/dev/null 2>&1 || docker build -q -t aetherion-openscad /mnt/nv/ia/aetherion-3d/sandbox
  [[ -f /mnt/nv/ia/llm/Qwen3.8-27B-IQ4_XS.gguf ]] || echo "⚠️  Falta la IA oficial de la web (/mnt/nv/ia/llm/Qwen3.8-27B-IQ4_XS.gguf): Aetherion 3D no funcionará hasta descargarla"
fi
if [[ -f "$ORIGEN/aetherion-asistente/ia-asistente.sh" ]]; then
  echo "==> Aether (asistente): IA pequeña en la CPU…"
  install -m 755 "$ORIGEN/aetherion-asistente/ia-asistente.sh" /usr/local/bin/ia-asistente.sh
  docker image inspect ghcr.io/ggml-org/llama.cpp:server >/dev/null 2>&1 || docker pull -q ghcr.io/ggml-org/llama.cpp:server
  if ls /mnt/nv/ia/llm/Qwen3-4B-Instruct-2507*Q5_K_M.gguf >/dev/null 2>&1; then
    runuser -u "$USUARIO" -- /usr/local/bin/ia-asistente.sh arrancar || true
  else
    echo "⚠️  Falta el modelo de Aether. Descárgalo con:  ia-asistente.sh descargar   (unos 3 GB)"
  fi
fi
umask 077
cat > /etc/aetherion-worker.env <<EOF
AETH_WEB=$WEB
AETH_WORKER_CLAVE=$CLAVE
EOF
chmod 600 /etc/aetherion-worker.env

cat > /etc/systemd/system/aetherion-worker.service <<EOF
[Unit]
Description=Aetherion Diseño — worker de GPUs (solo conexiones salientes)
After=network-online.target docker.service nvidia-pm.service
Wants=network-online.target

[Service]
User=$USUARIO
Group=$USUARIO
SupplementaryGroups=docker
EnvironmentFile=/etc/aetherion-worker.env
ExecStart=/usr/bin/python3 /opt/aetherion-worker/worker.py
Restart=always
RestartSec=15
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable aetherion-worker
systemctl restart aetherion-worker
sleep 3
systemctl --no-pager --lines=5 status aetherion-worker || true
echo
echo "✅ Instalado. Ver lo que hace:   journalctl -u aetherion-worker -f"
echo "   Parar / arrancar:            sudo systemctl stop|start aetherion-worker"
