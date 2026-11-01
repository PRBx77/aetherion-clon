# Aetherion Clon

Plataforma web Flask + worker cloud + 3D + voz clonada + asistente movil Aether.

Toda la infraestructura corre en 1 VPS + APIs cloud. No requiere GPU ni servidores locales.

## Arquitectura

| Pieza | Donde corre |
|---|---|
| Web Flask (gunicorn) | VPS 127.0.0.1:8200 |
| Worker cloud (3 hilos) | VPS, mismo servidor |
| LLM 3D (texto -> OpenSCAD) | OpenRouter (Qwen 2.5 Coder 32B) |
| LLM asistente Aether | OpenRouter (Llama 3.3 70B) |
| TTS voces estandar (ES/EN) | Piper local (CPU) |
| TTS voces clonadas | Replicate (XTTS-v2) |
| Avatar foto->video hablando | Replicate (SadTalker) |

## 3 niveles de acceso NFT

| Nivel | Max | Beneficios |
|---|---|---|
| Diamante | 50 | Todo (voz clonada + 3D + video + asistente) |
| Esmeralda | 100 | Voz estandar + 3D + video |
| Rubi | 150 | Web basica + asistente |

## Instalacion

```bash
# 1. Web Flask
sudo bash despliegue/instalar-vps-compartido.sh

# 2. Worker cloud
sudo bash despliegue/instalar-worker-cloud.sh

# 3. Editar /opt/aetherion-worker-cloud/.env y poner:
#    - AETH_WORKER_CLAVE   (misma de /opt/aetherion-clon/.env)
#    - OPENROUTER_API_KEY  (https://openrouter.ai/keys)
#    - REPLICATE_API_TOKEN (https://replicate.com/account/api-tokens)

# 4. Arrancar
systemctl start aetherion-worker-cloud
```

## Estructura del repo

- `web/` - Flask app (gunicorn, SQLite)
- `worker-cloud/` - worker.py + handler_3d.py + handler_video.py + handler_asistente.py
- `despliegue/` - scripts instalacion VPS + worker
- `produccion/` - systemd + nginx

## Seguridad

- Worker outbound-only (no abre puertos)
- Filtro de armas en handler_3d.py
- systemd ProtectSystem=strict, PrivateTmp=true, MemoryMax=512M
- CSP estricta, CSRF, cookies HTTPS-only
- Secretos solo en .env (nunca commits)

## Costes orientativos (beta familiar)

| Servicio | ~EUR/mes |
|---|---|
| VPS Hetzner | ya pagado |
| OpenRouter (LLM) | 2-10 |
| Replicate (SadTalker + XTTS) | 10-30 |
| **Total** | **15-40** |

Comparado con 40-65 EUR/mes solo de luz de un rig con GPU local 24/7.
