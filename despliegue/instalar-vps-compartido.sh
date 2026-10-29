#!/usr/bin/env bash
# Aetherion Diseño en el VPS de DiamondWall (modo COMPARTIDO, aislado).
#
#   sudo bash instalar-vps-compartido.sh            (primera vez y actualizaciones)
#
# Respeta las normas del VPS:
#   - nada bajo /opt/aetherion/, no se toca el vhost "aetherion", no se reinicia aetherion-api
#   - sin apt, sin Docker, sin cambios en ufw
#   - usuario propio "aetherion-clon" sin shell, gunicorn en 127.0.0.1:8200
#   - cloudflared: solo se AÑADE el hostname nuevo (con copia y validación) y se reinicia una vez
# Si una comprobación falla, se deshace el último cambio y se para.
set -euo pipefail

HOST=TU_DOMINIO.ejemplo.com
PRINCIPAL=https://TU_WEB_PRINCIPAL.ejemplo.com
PUERTO=8200
BASE=/opt/aetherion-clon
USR=aetherion-clon
ENVF=$BASE/.env
VHOST=/etc/nginx/sites-available/aetherion-clon
CFG=/etc/cloudflared/config.yml
COPIAS=/root/backups/aetherion-clon
ORIGEN="$(cd "$(dirname "$0")/.." && pwd)"
SELLO=$(date +%Y%m%d-%H%M%S)

paso() { echo -e "\n\033[1;36m==> $*\033[0m"; }
ok()   { echo -e "   \033[32m✔ $*\033[0m"; }
mal()  { echo -e "   \033[31m✘ $*\033[0m"; }
[[ $EUID -ne 0 ]] && { echo "Ejecuta con sudo"; exit 1; }
[[ -f "$ORIGEN/web/app.py" ]] || { echo "No encuentro $ORIGEN/web/app.py"; exit 1; }

principal_ok() { curl -fsS -o /dev/null -m 15 "$PRINCIPAL/" ; }

paso "0/7 Comprobaciones previas"
principal_ok && ok "DiamondWall responde ($PRINCIPAL)" || { mal "DiamondWall NO responde ahora mismo: no sigo"; exit 1; }
if ss -tln | grep -q "127.0.0.1:$PUERTO " && ! systemctl is-active --quiet aetherion-clon; then
  mal "El puerto $PUERTO está ocupado por otro servicio"; exit 1; fi
python3 -c "import venv, ensurepip" 2>/dev/null && ok "python3-venv disponible" || { mal "Falta python3-venv (apt install python3-venv)"; exit 1; }
libre=$(free -m | awk '/Mem:/{print $7}'); ok "RAM disponible: ${libre} MiB"
read -r -p "   ¿Has hecho el snapshot del VPS en Hetzner? (s/N) " r; [[ "$r" =~ ^[sS]$ ]] || { echo "Hazlo primero."; exit 1; }

paso "1/7 Usuario y carpetas aisladas"
id "$USR" &>/dev/null || useradd --system --home "$BASE" --shell /usr/sbin/nologin "$USR"
install -d -m 755 "$BASE"
install -d -o "$USR" -g "$USR" -m 750 "$BASE/data"
install -d -m 700 "$COPIAS"
ok "$USR, $BASE/data"

paso "2/7 Código y entorno Python propio"
rm -rf "$BASE/web.nuevo" && cp -r "$ORIGEN/web" "$BASE/web.nuevo"
rm -rf "$BASE/web.nuevo/datos" "$BASE/web.nuevo/.venv"; find "$BASE/web.nuevo" -name __pycache__ -prune -exec rm -rf {} +
[[ -d "$BASE/web" ]] && { rm -rf "$BASE/web.anterior"; mv "$BASE/web" "$BASE/web.anterior"; }
mv "$BASE/web.nuevo" "$BASE/web"
[[ -d "$BASE/venv" ]] || python3 -m venv "$BASE/venv"
"$BASE/venv/bin/pip" install -q --upgrade pip
"$BASE/venv/bin/pip" install -q -r "$BASE/web/requisitos.txt"
chown -R root:root "$BASE/web" "$BASE/venv"; chmod -R a+rX "$BASE/web" "$BASE/venv"
ok "venv en $BASE/venv"

paso "3/7 Secretos (solo la primera vez)"
if [[ ! -f "$ENVF" ]]; then
  umask 077
  cat > "$ENVF" <<EOF
AETH_DATOS=$BASE/data
AETH_SECRETO=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
AETH_WORKER_CLAVE=$(python3 -c 'import secrets;print(secrets.token_hex(32))')
AETH_HTTPS=1
AETH_PROXY=1
AETH_MAX_VIDEO_MB=95
EOF
  umask 022
fi
chown "$USR:$USR" "$ENVF"; chmod 600 "$ENVF"
ok "$ENVF (600, $USR)"

paso "4/7 Servicio aetherion-clon (gunicorn en 127.0.0.1:$PUERTO)"
cat > /etc/systemd/system/aetherion-clon.service <<EOF
[Unit]
Description=Aetherion Diseño (aetherion-clon) — web
After=network.target

[Service]
User=$USR
Group=$USR
EnvironmentFile=$ENVF
WorkingDirectory=$BASE/web
ExecStart=$BASE/venv/bin/gunicorn --workers 2 --threads 4 --timeout 300 --bind 127.0.0.1:$PUERTO --access-logfile - app:app
Restart=always
RestartSec=5
MemoryMax=600M
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=$BASE/data

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable --quiet aetherion-clon
systemctl restart aetherion-clon
sleep 3
curl -fsS -o /dev/null "http://127.0.0.1:$PUERTO/" && ok "La web responde en local" \
  || { mal "La web no arranca:"; journalctl -u aetherion-clon -n 30 --no-pager; exit 1; }

paso "5/7 nginx: sitio nuevo (no se toca el de DiamondWall)"
cat > "$VHOST.nuevo" <<EOF
# Aetherion Diseño — solo se llega a través de Cloudflare Tunnel (cloudflared corre en este VPS)
server {
    listen 80;
    server_name $HOST;

    access_log /var/log/nginx/aetherion-clon.access.log;
    error_log  /var/log/nginx/aetherion-clon.error.log;

    # Quien entre por la IP pública directamente (saltándose Cloudflare) recibe 403:
    allow 127.0.0.1;
    allow ::1;
    deny all;

    client_max_body_size 96m;

    location / {
        proxy_pass http://127.0.0.1:$PUERTO;
        proxy_set_header Host \$host;
        # IP real del visitante (la pone Cloudflare; fiable porque solo aceptamos tráfico local)
        proxy_set_header X-Forwarded-For \$http_cf_connecting_ip;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-Host \$host;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_request_buffering off;
    }
}
EOF
[[ -f "$VHOST" ]] && cp "$VHOST" "$VHOST.antes-$SELLO"
mv "$VHOST.nuevo" "$VHOST"
ln -sf "$VHOST" /etc/nginx/sites-enabled/aetherion-clon
if nginx -t 2>/tmp/nginx-t.log; then
  systemctl reload nginx
  sleep 1
  if principal_ok; then ok "nginx recargado; DiamondWall sigue respondiendo"
  else mal "DiamondWall dejó de responder: deshago"; rm -f /etc/nginx/sites-enabled/aetherion-clon; systemctl reload nginx; exit 1; fi
else
  mal "Configuración de nginx no válida: deshago"; cat /tmp/nginx-t.log
  rm -f /etc/nginx/sites-enabled/aetherion-clon; exit 1
fi

paso "6/7 Cloudflare Tunnel: añadir $HOST"
if grep -q "hostname: $HOST" "$CFG"; then
  ok "Ya estaba en el túnel; no se reinicia cloudflared"
else
  cp "$CFG" "$CFG.antes-$SELLO"
  python3 - "$CFG" "$HOST" <<'PY'
import sys, re
ruta, host = sys.argv[1], sys.argv[2]
lineas = open(ruta).read().splitlines(keepends=True)
# Insertar justo antes de la regla catch-all "- service: http_status:404"
for i, l in enumerate(lineas):
    if re.match(r"\s*-\s*service:\s*http_status:404", l):
        sangria = l[:len(l) - len(l.lstrip())]
        nuevo = [f"{sangria}- hostname: {host}\n", f"{sangria}  service: http://localhost:80\n"]
        lineas[i:i] = nuevo
        break
else:
    sys.exit("No encuentro la regla catch-all http_status:404 en config.yml")
open(ruta, "w").write("".join(lineas))
PY
  if cloudflared tunnel --config "$CFG" ingress validate >/dev/null 2>&1; then
    systemctl restart cloudflared
    sleep 6
    if principal_ok; then ok "cloudflared reiniciado; DiamondWall sigue respondiendo"
    else
      mal "DiamondWall no responde tras reiniciar cloudflared: restauro la config anterior"
      cp "$CFG.antes-$SELLO" "$CFG"; systemctl restart cloudflared; sleep 6
      principal_ok && ok "DiamondWall recuperado" || mal "¡Revisa cloudflared YA!: journalctl -u cloudflared -n 50"
      exit 1
    fi
  else
    mal "La configuración del túnel no valida: restauro"; cp "$CFG.antes-$SELLO" "$CFG"; exit 1
  fi
fi

paso "7/7 Copia diaria de la base de datos y atajo de administración"
cat > /etc/cron.daily/aetherion-clon-copia <<EOF
#!/bin/sh
$BASE/venv/bin/python -c "import sqlite3,datetime;s=sqlite3.connect('$BASE/data/aetherion.db');d=sqlite3.connect('$COPIAS/aetherion-'+datetime.date.today().isoformat()+'.db');s.backup(d);d.close()"
find $COPIAS -name 'aetherion-*.db' -mtime +14 -delete
EOF
chmod 755 /etc/cron.daily/aetherion-clon-copia
cat > /usr/local/bin/aetherion-clon-admin <<EOF
#!/bin/sh
# Administración de Aetherion Diseño: aetherion-clon-admin invitar --nivel diamante --creditos 20
cd $BASE/web && exec runuser -u $USR -- env \$(grep -v '^#' $ENVF | xargs) $BASE/venv/bin/python admin.py "\$@"
EOF
chmod 700 /usr/local/bin/aetherion-clon-admin
ok "Copias en $COPIAS (14 días) · comando: aetherion-clon-admin"

echo
echo "──────────────────────────────────────────────────────────────"
echo " Falta UN paso en Cloudflare (DNS → Records), si no lo has hecho:"
echo "   CNAME  aetherion-clon  →  $(awk '/^tunnel:/{print $2}' $CFG).cfargotunnel.com   (Proxied, nube naranja)"
echo
echo " Dirección:        https://$HOST"
echo " Clave del worker: $(grep AETH_WORKER_CLAVE $ENVF | cut -d= -f2)"
echo "   (es para el X99; no la compartas)"
echo
if [[ -z "$(aetherion-clon-admin usuarios)" ]]; then
  echo " Tu primera invitación:"
  aetherion-clon-admin invitar --nivel diamante --creditos 100 --dias 7 --nota "Pablo admin"
  echo
  echo " Tras registrarte:  aetherion-clon-admin hacer-admin TU_USUARIO"
fi
echo "──────────────────────────────────────────────────────────────"
