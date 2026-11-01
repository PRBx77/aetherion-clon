"""Worker cloud COMPLETO: 3D + vídeo + asistente."""
import os, sys, time, requests, logging, traceback, threading
from handler_3d import procesar_3d
from handler_video import procesar_video
from handler_asistente import procesar_asistente

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s', stream=sys.stdout)
log = logging.getLogger("worker-cloud")

BASE = os.environ.get("AETH_URL_BASE", "http://127.0.0.1:8200").rstrip("/")
CLAVE = os.environ.get("AETH_WORKER_CLAVE", "")
PAUSA = int(os.environ.get("AETH_PAUSA", "5"))
HEADERS = {"Authorization": f"Bearer {CLAVE}", "User-Agent": "aetherion-worker-cloud/2"}

def loop_trabajos():
    """Coge 3D y vídeos de /worker/siguiente."""
    log.info("Hilo trabajos (3D + vídeo) arrancado")
    while True:
        try:
            r = requests.post(f"{BASE}/worker/siguiente", headers=HEADERS, timeout=15)
            if r.status_code == 204:
                time.sleep(PAUSA); continue
            if r.status_code == 401:
                log.error("401 trabajos: revisa AETH_WORKER_CLAVE"); time.sleep(30); continue
            if r.status_code != 200:
                log.warning(f"Trabajos {r.status_code}: {r.text[:150]}"); time.sleep(10); continue
            j = r.json()
            tipo = j.get("tipo")
            if tipo == "3d":
                log.info(f"-> 3D {j['id']}: {j['texto'][:50]}")
                procesar_3d(j, BASE, HEADERS)
            elif tipo == "avatar":
                log.info(f"-> VÍDEO {j['id']}: {j['guion'][:50]}")
                procesar_video(j, BASE, HEADERS)
            else:
                log.warning(f"Tipo desconocido: {tipo}"); time.sleep(5)
        except Exception as e:
            log.error(f"Loop trabajos: {e}\n{traceback.format_exc()}")
            time.sleep(10)

def loop_asistente():
    """Long-poll de /worker/asistente/siguiente."""
    log.info("Hilo asistente arrancado")
    while True:
        try:
            r = requests.post(f"{BASE}/worker/asistente/siguiente", headers=HEADERS, timeout=30)
            if r.status_code == 204:
                continue  # long-poll ya esperó
            if r.status_code == 401:
                log.error("401 asistente"); time.sleep(30); continue
            if r.status_code != 200:
                log.warning(f"Asist {r.status_code}: {r.text[:150]}"); time.sleep(5); continue
            j = r.json()
            log.info(f"-> ASIST {j['id']}: {j.get('texto','')[:50]}")
            procesar_asistente(j, BASE, HEADERS)
        except Exception as e:
            log.error(f"Loop asistente: {e}\n{traceback.format_exc()}")
            time.sleep(5)

if __name__ == "__main__":
    if not CLAVE:
        log.error("Falta AETH_WORKER_CLAVE"); sys.exit(1)
    log.info(f"Worker-cloud v2 arrancando. BASE={BASE}")
    t1 = threading.Thread(target=loop_trabajos, daemon=True)
    t2 = threading.Thread(target=loop_asistente, daemon=True)
    t1.start(); t2.start()
    while True: time.sleep(60)
