#!/usr/bin/env python3
"""Avatar parlante en paralelo (SadTalker en varias GPUs) — aetherion-1.

Uso:
  avatar.py --imagen foto.png --audio voz.wav --salida video.mp4 [--calidad rapida|maxima] [--gpus 0,1,2]

Corta el audio en silencios, genera cada trozo en una GPU distinta y une el resultado
con el audio original completo.
"""
import argparse
import fcntl
import json
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

APP = Path("/mnt/nv/ia/sadtalker")          # se monta en /app dentro del contenedor
CACHE = Path("/mnt/nv/cache/sadtalker")
IMAGEN_DOCKER = "sadtalker:pascal"
MIN_SEG_POR_GPU = 4.0                       # trozos más cortos no compensan el arranque
CANDADO_GPU = Path("/mnt/nv/ia/.gpus.lock")  # suite y worker web se turnan las GPUs

DIVIDIR = r'''
import json, sys
from pydub import AudioSegment
from pydub.silence import detect_silence
a = AudioSegment.from_file(sys.argv[1]).set_channels(1)
n = int(sys.argv[2]); L = len(a)
sil = detect_silence(a, min_silence_len=180, silence_thresh=a.dBFS - 16)
medios = [(s + e) // 2 for s, e in sil]
cortes = []
for k in range(1, n):
    obj = L * k // n; ventana = L // (2 * n)
    cand = [m for m in medios if abs(m - obj) <= ventana and (not cortes or m > cortes[-1] + 1000)]
    cortes.append(min(cand, key=lambda m: abs(m - obj)) if cand else obj)
lim = [0] + cortes + [L]
partes = []
for i in range(n):
    p = f"{sys.argv[3]}/parte{i}.wav"
    a[lim[i]:lim[i + 1]].set_frame_rate(16000).export(p, format="wav")
    partes.append({"archivo": p, "ms": lim[i + 1] - lim[i]})
print(json.dumps({"total_ms": L, "partes": partes, "silencios_usados": cortes}))
'''


def base_cmd():
    uid = f"{subprocess.getoutput('id -u')}:{subprocess.getoutput('id -g')}"
    return ["docker", "run", "--rm", "--user", uid,
            "-e", "HOME=/cache", "-e", "NUMBA_CACHE_DIR=/cache/numba", "-e", "MPLCONFIGDIR=/cache/mpl",
            "-e", "PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128",
            "-v", f"{APP}:/app", "-v", f"{CACHE}:/cache"]


def docker(args, **kw):
    """Ejecuta un comando en el contenedor sin GPU (dividir audio, unir vídeo)."""
    return subprocess.run(base_cmd() + [IMAGEN_DOCKER] + args, **kw)


def lanzar(args, gpu, log):
    """Lanza SadTalker en una GPU concreta, en segundo plano."""
    return subprocess.Popen(base_cmd() + ["--gpus", f"device={gpu}", IMAGEN_DOCKER] + args,
                            stdout=log, stderr=subprocess.STDOUT)


def main():
    ap = argparse.ArgumentParser(description="Avatar parlante en varias GPUs")
    ap.add_argument("--imagen", required=True)
    ap.add_argument("--audio", required=True)
    ap.add_argument("--salida", required=True)
    ap.add_argument("--calidad", choices=["rapida", "maxima"], default="rapida")
    ap.add_argument("--gpus", default="0,1,2")
    ap.add_argument("--preprocess", default="full", choices=["full", "crop", "extfull", "resize"])
    ap.add_argument("--sin-still", action="store_true", help="permitir más movimiento de cabeza")
    a = ap.parse_args()

    gpus = [g.strip() for g in a.gpus.split(",") if g.strip()]
    candado = open(CANDADO_GPU, "a")
    try:
        fcntl.flock(candado, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[0/4] GPUs ocupadas con otro trabajo, esperando turno…", flush=True)
        fcntl.flock(candado, fcntl.LOCK_EX)
    t0 = time.time()
    tid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    trabajo = APP / "trabajos" / tid
    trabajo.mkdir(parents=True)
    img = trabajo / ("imagen" + Path(a.imagen).suffix.lower())
    aud = trabajo / ("audio" + Path(a.audio).suffix.lower())
    shutil.copy(a.imagen, img)
    shutil.copy(a.audio, aud)
    rel = f"trabajos/{tid}"
    print(f"[1/4] Trabajo {tid}")

    # Duración y número de trozos
    r = docker(["python3", "-c", "import sys;from pydub import AudioSegment as A;print(len(A.from_file(sys.argv[1])))",
                f"/app/{rel}/{aud.name}"], capture_output=True, text=True)
    if r.returncode:
        sys.exit("No se pudo leer el audio:\n" + r.stderr[-800:])
    dur = int(r.stdout.strip()) / 1000
    n = max(1, min(len(gpus), int(dur // MIN_SEG_POR_GPU)))
    print(f"[2/4] Audio de {dur:.1f} s → {n} trozo(s) en GPU {', '.join(gpus[:n])}")

    r = docker(["python3", "-c", DIVIDIR, f"/app/{rel}/{aud.name}", str(n), f"/app/{rel}"],
               capture_output=True, text=True)
    if r.returncode:
        sys.exit("Error al dividir el audio:\n" + r.stderr[-800:])
    info = json.loads(r.stdout.strip().splitlines()[-1])
    for i, p in enumerate(info["partes"]):
        print(f"      trozo {i}: {p['ms'] / 1000:.1f} s")

    size, lote = ("256", "2") if a.calidad == "rapida" else ("512", "1")
    procs = []
    for i in range(n):
        args = ["python3", "inference.py", "--driven_audio", f"{rel}/parte{i}.wav",
                "--source_image", f"{rel}/{img.name}", "--result_dir", f"{rel}/sal{i}",
                "--size", size, "--batch_size", lote, "--enhancer", "gfpgan",
                "--preprocess", a.preprocess]
        if not a.sin_still:
            args.append("--still")
        log = open(trabajo / f"gpu{gpus[i]}.log", "w")
        procs.append((i, lanzar(args, gpus[i], log), log))
    print(f"[3/4] Generando en {n} GPU(s) a {size} px… (registros en {trabajo})")
    fallos = []
    for i, p, log in procs:
        p.wait(); log.close()
        print(f"      trozo {i}: {'OK' if p.returncode == 0 else 'ERROR'}  ({(time.time() - t0) / 60:.1f} min)")
        if p.returncode:
            fallos.append(i)
    if fallos:
        sys.exit(f"Fallaron los trozos {fallos}. Mira {trabajo}/gpu*.log")

    clips = []
    for i in range(n):
        mp4 = sorted((trabajo / f"sal{i}").glob("*.mp4"))
        if not mp4:
            sys.exit(f"No se encontró el vídeo del trozo {i}")
        clips.append(mp4[-1])
    lista = trabajo / "lista.txt"
    lista.write_text("".join(f"file '/app/{rel}/{c.relative_to(trabajo)}'\n" for c in clips))

    print("[4/4] Uniendo clips con el audio original…")
    r = docker(["sh", "-c",
                f"ffmpeg -y -loglevel error -f concat -safe 0 -i /app/{rel}/lista.txt -an "
                f"-c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p /app/{rel}/video_sin_audio.mp4 && "
                f"ffmpeg -y -loglevel error -i /app/{rel}/video_sin_audio.mp4 -i /app/{rel}/{aud.name} "
                f"-map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k -shortest /app/{rel}/final.mp4"],
               capture_output=True, text=True)
    if r.returncode:
        sys.exit("Error al unir:\n" + r.stderr[-800:])
    destino = Path(a.salida).expanduser().resolve()
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(trabajo / "final.mp4", destino)
    print(f"LISTO en {(time.time() - t0) / 60:.1f} min → {destino}")


if __name__ == "__main__":
    main()
