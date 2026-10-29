#!/usr/bin/env bash
# Actualiza SOLO el código de la web aetherion-clon en el VPS (no toca nginx, Cloudflare, secretos ni datos).
#   bash aetherion/despliegue/actualizar-web.sh
# Si algo falla, vuelve sola a la versión anterior.
set -euo pipefail
BASE=/opt/aetherion-clon; PUERTO=8200; PRINCIPAL="https://TU_WEB_PRINCIPAL.ejemplo.com"
ORIGEN="$(cd "$(dirname "$0")/.." && pwd)"
[[ $EUID -ne 0 ]] && { echo "Ejecuta como root"; exit 1; }
[[ -d "$BASE/web" && -f "$ORIGEN/web/app.py" ]] || { echo "No encuentro la instalación o el paquete"; exit 1; }

echo "==> Copia de seguridad de la base de datos"
"$BASE/venv/bin/python" -c "import sqlite3;s=sqlite3.connect('$BASE/data/aetherion.db');d=sqlite3.connect('/root/backups/aetherion-clon/antes-actualizar-$(date +%Y%m%d-%H%M).db');s.backup(d)"

echo "==> Código nuevo"
rm -rf "$BASE/web.nuevo" && cp -r "$ORIGEN/web" "$BASE/web.nuevo"
rm -rf "$BASE/web.nuevo/datos"; find "$BASE/web.nuevo" -name __pycache__ -prune -exec rm -rf {} +
"$BASE/venv/bin/pip" install -q -r "$BASE/web.nuevo/requisitos.txt"
rm -rf "$BASE/web.anterior"; mv "$BASE/web" "$BASE/web.anterior"; mv "$BASE/web.nuevo" "$BASE/web"
chown -R root:root "$BASE/web"; chmod -R a+rX "$BASE/web"
systemctl restart aetherion-clon; sleep 3

if curl -fsS -o /dev/null "http://127.0.0.1:$PUERTO/" && curl -fsS -o /dev/null "http://127.0.0.1:$PUERTO/asistente/manifest.webmanifest"; then
  echo "✅ Web actualizada"
else
  echo "❌ La versión nueva no arranca: vuelvo a la anterior"
  journalctl -u aetherion-clon -n 20 --no-pager
  rm -rf "$BASE/web.fallida"; mv "$BASE/web" "$BASE/web.fallida"; mv "$BASE/web.anterior" "$BASE/web"
  systemctl restart aetherion-clon; exit 1
fi
curl -fsS -o /dev/null -m 15 "$PRINCIPAL/" && echo "✅ DiamondWall sigue respondiendo" || echo "⚠️  DiamondWall no responde: revísalo (no lo hemos tocado)"
