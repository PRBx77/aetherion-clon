#!/bin/bash
# Instala el worker-cloud (3D + vídeo + asistente) en el mismo VPS.
# Requiere que la web ya esté instalada en /opt/aetherion-clon
set -e

echo ">> 1/6 Dependencias sistema"
apt update
apt install -y openscad xvfb python3-venv python3-pip ffmpeg wget

echo ">> 2/6 Usuario de servicio"
id aetherion-worker &>/dev/null || useradd -r -s /usr/sbin/nologin -d /opt/aetherion-worker-cloud aetherion-worker

echo ">> 3/6 Estructura"
mkdir -p /opt/aetherion-worker-cloud/{temp,logs,voces_tts}
cp worker-cloud/worker.py worker-cloud/handler_*.py /opt/aetherion-worker-cloud/
[ -f /opt/aetherion-worker-cloud/.env ] || cp worker-cloud/.env.ejemplo /opt/aetherion-worker-cloud/.env

echo ">> 4/6 Entorno virtual + libs"
python3 -m venv /opt/aetherion-worker-cloud/venv
/opt/aetherion-worker-cloud/venv/bin/pip install --upgrade pip
/opt/aetherion-worker-cloud/venv/bin/pip install -r worker-cloud/requirements.txt

echo ">> 5/6 Voces TTS (Piper)"
cd /opt/aetherion-worker-cloud/voces_tts
BASE=https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0
wget -q "$BASE/es/es_ES/carlfm/x_low/es_ES-carlfm-x_low.onnx"
wget -q "$BASE/es/es_ES/carlfm/x_low/es_ES-carlfm-x_low.onnx.json"
wget -q "$BASE/en/en_US/ryan/high/en_US-ryan-high.onnx"
wget -q "$BASE/en/en_US/ryan/high/en_US-ryan-high.onnx.json"
cd - >/dev/null

echo ">> 6/6 systemd"
cp worker-cloud/aetherion-worker-cloud.service /etc/systemd/system/
chown -R aetherion-worker:aetherion-worker /opt/aetherion-worker-cloud
chmod 600 /opt/aetherion-worker-cloud/.env
systemctl daemon-reload
systemctl enable aetherion-worker-cloud

echo ""
echo "LISTO. Edita /opt/aetherion-worker-cloud/.env con:"
echo "  - AETH_WORKER_CLAVE   (mismo valor que /opt/aetherion-clon/.env)"
echo "  - OPENROUTER_API_KEY  (https://openrouter.ai/keys)"
echo "  - REPLICATE_API_TOKEN (https://replicate.com/account/api-tokens)"
echo ""
echo "Y luego:  systemctl start aetherion-worker-cloud"
