# Aetherion Clon

Plataforma web Flask + worker GPU outbound-only + modelado 3D texto→STL + voz clonada + asistente CPU.

## Arquitectura

- `web/` — Flask (gunicorn, 127.0.0.1:8200, SQLite)
- `worker/` — Trabajador outbound-only (SadTalker + Chatterbox) para rig GPU separado
- `aetherion-3d/` — Texto → OpenSCAD → STL con LLM local
- `aetherion-asistente/` — Asistente CPU Qwen3-4B
- `despliegue/` — Scripts de instalación VPS/worker/actualización
- `produccion/` — systemd service + nginx vhost de ejemplo

## 3 niveles de acceso

| Nivel | Beneficios |
|---|---|
| Diamante | Todo (voz clonada + 3D + asistente) |
| Esmeralda | Voz + 3D |
| Rubí | Web básica + asistente |

## Variables de entorno

Ver cabecera de `web/app.py`. Las claves se generan automáticamente con `secrets.token_hex(32)` en `despliegue/instalar-vps-compartido.sh`.

## Instalación

```bash
# VPS
sudo bash despliegue/instalar-vps-compartido.sh

# Rig GPU
sudo bash despliegue/instalar-worker.sh
```

## Seguridad

- Worker solo salida HTTPS (no abre puertos)
- Filtro `filtro.py` bloquea peticiones de armas/explosivos
- systemd `ProtectSystem=strict`, `PrivateTmp=true`, `MemoryMax=600M`
- CSP estricta, CSRF, cookies HTTPS-only
