"""Handler vídeo (fotos → vídeo hablando): Piper/XTTS + SadTalker + opcional GFPGAN."""
import os, subprocess, tempfile, logging, requests, replicate, time
from pathlib import Path
log = logging.getLogger("handler-video")

REPLICATE_TOKEN = os.environ.get("REPLICATE_API_TOKEN", "")
os.environ["REPLICATE_API_TOKEN"] = REPLICATE_TOKEN

TEMP_DIR = Path("/opt/aetherion-worker-cloud/temp")
VOCES_DIR = Path("/opt/aetherion-worker-cloud/voces_tts")

VOZ_PIPER = {
    "es": VOCES_DIR / "es_ES-carlfm-x_low.onnx",
    "en": VOCES_DIR / "en_US-ryan-high.onnx",
}

MODEL_SADTALKER = "cjwbw/sadtalker:3aa3dac9353cc4d6bd62a8f95957bd844003b401ca4e4a9b33baa574c549d376"
MODEL_XTTS      = "lucataco/xtts-v2:684bc3855b37866c0c65add2ff39c78f3dea3f4ff103a436465326e0f438d55e"
MODEL_GFPGAN    = "tencentarc/gfpgan:0fbacf7afc6c144e5be9767cff80f25aff23e52b0708f17e20f9879b2f21516c"

def _progreso(base, headers, tid, p, fase):
    try:
        requests.post(f"{base}/worker/progreso/{tid}", headers=headers,
                      json={"progreso": p, "fase": fase[:60]}, timeout=10)
    except Exception as e:
        log.warning(f"progreso: {e}")

def _error(base, headers, tid, msg):
    try:
        requests.post(f"{base}/worker/error/{tid}", headers=headers,
                      json={"error": msg[:300]}, timeout=10)
    except Exception as e:
        log.warning(f"error-rep: {e}")

def _descargar(url, destino, headers=None):
    r = requests.get(url, headers=headers or {}, stream=True, timeout=60)
    r.raise_for_status()
    with open(destino, "wb") as f:
        for chunk in r.iter_content(1024*32):
            f.write(chunk)

def _tts_piper(texto, idioma, salida_wav):
    """TTS local con Piper para voces preset."""
    voz = VOZ_PIPER.get(idioma, VOZ_PIPER["es"])
    if not voz.exists():
        raise RuntimeError(f"Voz Piper no encontrada: {voz}")
    r = subprocess.run(["/opt/aetherion-worker-cloud/venv/bin/python3", "-m", "piper",
                        "--model", str(voz), "--output_file", str(salida_wav)],
                       input=texto, text=True, capture_output=True, timeout=60)
    if r.returncode != 0 or not salida_wav.exists():
        raise RuntimeError(f"Piper: {r.stderr[:200]}")

def _tts_xtts(texto, idioma, muestra_wav, salida_wav):
    """TTS con voz clonada vía Replicate XTTS-v2."""
    with open(muestra_wav, "rb") as f_muestra:
        out = replicate.run(MODEL_XTTS, input={
            "text": texto, "language": idioma, "speaker": f_muestra, "cleanup_voice": True
        })
    _descargar(out, salida_wav)

def _sadtalker(foto, audio, salida_mp4, calidad="rapida"):
    with open(foto, "rb") as f_img, open(audio, "rb") as f_aud:
        size = 256 if calidad == "rapida" else 512
        out = replicate.run(MODEL_SADTALKER, input={
            "source_image": f_img, "driven_audio": f_aud,
            "preprocess": "crop", "still_mode": False, "use_enhancer": False, "image_size": size
        })
    _descargar(out, salida_mp4)

def _validar_mp4(path):
    with open(path, "rb") as f:
        cab = f.read(12)
    return len(cab) == 12 and cab[4:8] == b"ftyp"

def procesar_video(j, base, headers):
    tid = j["id"]
    guion = j["guion"]
    idioma = (j.get("idioma") or "es").lower()[:2]
    voz = j.get("voz") or ""
    calidad = j.get("calidad") or "rapida"
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=TEMP_DIR) as tmp:
            tmp = Path(tmp)
            foto = tmp / "foto.png"
            audio = tmp / "voz.wav"
            video = tmp / "video.mp4"

            _progreso(base, headers, tid, 10, "Descargando foto")
            _descargar(f"{base}/worker/foto/{tid}", foto, headers)

            _progreso(base, headers, tid, 25, "Generando voz")
            if voz.startswith("clon:"):
                muestra = tmp / "muestra.wav"
                _descargar(f"{base}/worker/muestra/{tid}", muestra, headers)
                _tts_xtts(guion, idioma, muestra, audio)
            else:
                _tts_piper(guion, idioma, audio)

            _progreso(base, headers, tid, 55, "Generando vídeo (SadTalker)")
            _sadtalker(foto, audio, video, calidad)

            if not _validar_mp4(video):
                raise RuntimeError("MP4 inválido devuelto por SadTalker")

            _progreso(base, headers, tid, 95, "Subiendo")
            with open(video, "rb") as fv:
                r = requests.post(f"{base}/worker/resultado/{tid}",
                    headers={"Authorization": headers["Authorization"]},
                    files={"video": ("video.mp4", fv, "video/mp4")}, timeout=120)
                r.raise_for_status()
            log.info(f"Vídeo {tid} entregado ({video.stat().st_size // 1024} KB)")
    except Exception as e:
        log.error(f"Vídeo {tid}: {e}")
        _error(base, headers, tid, str(e))
