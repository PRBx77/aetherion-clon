"""Genera STL desde texto usando OpenRouter (LLM) + OpenSCAD + subida al API."""
import os, re, subprocess, tempfile, logging, requests, json, struct
from pathlib import Path
import numpy as np
from stl import mesh

log = logging.getLogger("handler-3d")

LLM_URL   = os.environ.get("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
LLM_KEY   = os.environ.get("OPENROUTER_API_KEY", "")
LLM_MODEL = os.environ.get("OPENROUTER_MODEL", "qwen/qwen-2.5-coder-32b-instruct")
TEMP_DIR  = Path("/opt/aetherion-worker-cloud/temp")

SISTEMA = """Eres experto en OpenSCAD. Genera SOLO código OpenSCAD paramétrico e imprimible para la petición.
REGLAS:
- SOLO código OpenSCAD, sin markdown, sin prosa, sin comentarios iniciales.
- Nunca armas, piezas peligrosas, explosivos ni réplicas de violencia.
- Pieza centrada y asentada en Z=0.
- Resolución $fn=64.
- Dimensiones razonables (máx. 150mm por lado) para impresora 3D doméstica.
- Usa union/difference/hull cuando convenga para formas limpias."""

PROHIBIDO = re.compile(r"\b(arma|pistola|revolver|fusil|rifle|escopeta|silenciador|bala|cartucho|munici|gun|firearm|trigger|shotgun|suppressor|ammo|bullet|explosiv|granad|grenade|nudillera|knuckle)\b", re.I)

def _progreso(base, headers, pid, p, fase):
    try:
        requests.post(f"{base}/worker/3d/{pid}/progreso", headers=headers,
                      json={"progreso": p, "fase": fase[:60]}, timeout=10)
    except Exception as e:
        log.warning(f"progreso: {e}")

def _error(base, headers, pid, msg):
    try:
        requests.post(f"{base}/worker/3d/{pid}/error", headers=headers,
                      json={"error": msg[:300]}, timeout=10)
    except Exception as e:
        log.warning(f"error-rep: {e}")

def _limpiar_scad(t):
    t = re.sub(r"^```\w*\s*", "", t.strip())
    t = re.sub(r"\s*```$", "", t)
    return t.strip()

def _llamar_llm(texto, previo_scad=None):
    msgs = [{"role": "system", "content": SISTEMA}]
    if previo_scad:
        msgs.append({"role": "user", "content": f"Modifica este modelo:\n```\n{previo_scad[:4000]}\n```\n\nPetición: {texto}"})
    else:
        msgs.append({"role": "user", "content": texto})
    r = requests.post(LLM_URL,
        headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"},
        json={"model": LLM_MODEL, "messages": msgs, "temperature": 0.3, "max_tokens": 1800},
        timeout=120)
    r.raise_for_status()
    return _limpiar_scad(r.json()["choices"][0]["message"]["content"])

def _info_stl(path):
    try:
        m = mesh.Mesh.from_file(str(path))
        v = m.vectors.reshape(-1, 3)
        bb = (v.max(axis=0) - v.min(axis=0)).tolist()
        vol = float(abs(m.get_mass_properties()[0]) / 1000.0)  # cm3
        return {
            "medidas_mm": [round(x, 1) for x in bb],
            "triangulos": int(len(m.vectors)),
            "volumen_cm3": round(vol, 2),
            "cerrado": True,
            "avisos": [],
            "intentos": 1,
            "piezas": 1,
            "llenado": 100
        }
    except Exception as e:
        log.warning(f"info_stl: {e}")
        return {}

def procesar_3d(j, base, headers):
    pid = j["id"]
    texto = j["texto"]
    if PROHIBIDO.search(texto):
        _error(base, headers, pid, "Petición no permitida (filtro de seguridad).")
        return
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        # Previo (si lo hay)
        previo_scad = None
        if j.get("previo"):
            try:
                r = requests.get(f"{base}/worker/3d/{pid}/previo", headers=headers, timeout=10)
                if r.status_code == 200:
                    previo_scad = r.text
            except Exception:
                pass

        _progreso(base, headers, pid, 15, "Pensando el modelo")
        scad = _llamar_llm(texto, previo_scad)
        log.info(f"SCAD generado ({len(scad)} chars)")

        _progreso(base, headers, pid, 55, "Compilando STL")
        with tempfile.TemporaryDirectory(dir=TEMP_DIR) as tmp:
            scad_f = Path(tmp) / "modelo.scad"
            stl_f  = Path(tmp) / "modelo.stl"
            png_f  = Path(tmp) / "vista.png"
            scad_f.write_text(scad)
            r = subprocess.run(["openscad", "-o", str(stl_f), str(scad_f)],
                             capture_output=True, timeout=240)
            if r.returncode != 0 or not stl_f.exists() or stl_f.stat().st_size < 84:
                err = (r.stderr.decode(errors="ignore") or "OpenSCAD falló")[:300]
                raise RuntimeError(f"OpenSCAD: {err}")

            _progreso(base, headers, pid, 80, "Vista previa")
            try:
                subprocess.run(["xvfb-run", "-a", "openscad", "--imgsize=800,600",
                              "--colorscheme=Tomorrow", "-o", str(png_f), str(scad_f)],
                             capture_output=True, timeout=90)
            except Exception as e:
                log.warning(f"preview: {e}")

            _progreso(base, headers, pid, 95, "Subiendo")
            info = _info_stl(stl_f)
            with open(stl_f, "rb") as fs, open(scad_f, "rb") as fc:
                files = [("stl", ("modelo.stl", fs, "model/stl")),
                         ("scad", ("modelo.scad", fc, "text/plain"))]
                if png_f.exists() and png_f.stat().st_size > 100:
                    files.append(("png", ("vista.png", open(png_f, "rb"), "image/png")))
                r = requests.post(f"{base}/worker/3d/{pid}/resultado",
                    headers={"Authorization": headers["Authorization"]},
                    files=files, data={"resultado": json.dumps(info)}, timeout=60)
                r.raise_for_status()
            log.info(f"Pieza {pid} entregada ({info.get('medidas_mm')}, {info.get('triangulos')} tri)")
    except Exception as e:
        log.error(f"Pieza {pid}: {e}")
        _error(base, headers, pid, str(e))
