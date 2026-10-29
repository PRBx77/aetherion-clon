#!/usr/bin/env python3
"""Aetherion Diseño — web pública (independiente del servidor de cálculo).

La web NUNCA se conecta al servidor de GPUs. El worker (X99) entra por HTTPS
con su clave, coge paquetes de la cola y devuelve los resultados.

Configuración por variables de entorno:
  AETH_DATOS         carpeta de datos (base de datos + archivos)   [./datos]
  AETH_SECRETO       clave para firmar sesiones (obligatoria en producción)
  AETH_WORKER_CLAVE  clave del worker (obligatoria)
  AETH_HTTPS         1 = cookies solo por HTTPS (producción)
"""
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from functools import wraps
from pathlib import Path

from flask import (Flask, abort, g, jsonify, make_response, redirect, render_template, request,
                   send_file, session, url_for)
from PIL import Image, ImageOps
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

DATOS = Path(os.environ.get("AETH_DATOS", Path(__file__).parent / "datos")).resolve()
DB = DATOS / "aetherion.db"
ARCHIVOS = DATOS / "usuarios"
WORKER_CLAVE = os.environ.get("AETH_WORKER_CLAVE", "")

NIVELES = {"diamante": "💎 Diamante", "esmeralda": "💚 Esmeralda", "rubi": "❤️ Rubí"}
# Lo que el worker sabe hacer. Si añades una voz en el X99, añádela aquí.
VOCES = {
    "es_ES-davefx-medium": ("es", "Español — Dave (hombre)"),
    "es_ES-carlfm-x_low": ("es", "Español — Carl (hombre, rápida)"),
    "en_US-ryan-high": ("en", "English — Ryan (high)"),
    "en_US-ryan-medium": ("en", "English — Ryan (medium)"),
}
IDIOMAS = {"es": "Español", "en": "English"}
# Ritmo de habla → length_scale de Piper (más alto = más lento)
RITMOS = {"pausado": ("Pausado", 1.35), "normal": ("Normal", 1.25), "agil": ("Ágil", 1.1)}
CALIDADES = {"rapida": ("Rápida (256 px)", 1), "maxima": ("Máxima (512 px)", 2)}
# Voz clonada (Chatterbox): ritmo → cfg_weight (más bajo = más pausado), expresividad → exaggeration
RITMO_CLON = {"pausado": 0.3, "normal": 0.45, "agil": 0.6}
EXPRESIVIDAD = {"neutra": ("Neutra", 0.35), "normal": ("Normal", 0.5), "expresiva": ("Expresiva", 0.7)}
COSTE_CLON = 1             # crédito extra por usar voz clonada
MAX_VOCES = 2              # voces clonadas por usuario
MAX_MUESTRA = 10 * 1024 * 1024
APROBAR_VOCES = os.environ.get("AETH_APROBAR_VOCES", "0") == "1"   # 1 = el admin revisa cada voz antes de usarla
ID_VOZ = re.compile(r"^v[0-9a-f]{12}$")
MAX_GUION = 600            # caracteres
MAX_FOTO = 8 * 1024 * 1024  # bytes
MAX_VIDEO = int(os.environ.get("AETH_MAX_VIDEO_MB", "95")) * 1024 * 1024   # Cloudflare gratis: 100 MB por petición
MAX_EN_COLA = 3            # trabajos pendientes por usuario
CADUCA_PROCESANDO = 60 * 60  # s: si el worker no responde, vuelve a la cola

app = Flask(__name__, template_folder="plantillas", static_folder="estatico")
app.config.update(
    SECRET_KEY=os.environ.get("AETH_SECRETO") or secrets.token_hex(32),
    MAX_CONTENT_LENGTH=MAX_VIDEO + 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("AETH_HTTPS") == "1",
    PERMANENT_SESSION_LIFETIME=14 * 24 * 3600,
)
if os.environ.get("AETH_PROXY") == "1":
    # Detrás de Caddy (un solo proxy): IP real, https y dominio vienen en las cabeceras X-Forwarded-*
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# ───────────────────────── base de datos ─────────────────────────
ESQUEMA = """
CREATE TABLE IF NOT EXISTS usuarios (
  id INTEGER PRIMARY KEY, nombre TEXT UNIQUE NOT NULL COLLATE NOCASE, hash TEXT NOT NULL,
  nivel TEXT NOT NULL, creditos INTEGER NOT NULL DEFAULT 0,
  origen TEXT NOT NULL DEFAULT 'invitacion', wallet TEXT UNIQUE,
  activo INTEGER NOT NULL DEFAULT 1, creado INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS invitaciones (
  codigo TEXT PRIMARY KEY, nivel TEXT NOT NULL, creditos INTEGER NOT NULL,
  caduca INTEGER NOT NULL, nota TEXT, usado_por INTEGER, usado_en INTEGER,
  revocada INTEGER NOT NULL DEFAULT 0, creado INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS trabajos (
  id TEXT PRIMARY KEY, usuario_id INTEGER NOT NULL, estado TEXT NOT NULL,
  progreso INTEGER NOT NULL DEFAULT 0, fase TEXT, guion TEXT NOT NULL,
  idioma TEXT NOT NULL, voz TEXT NOT NULL, calidad TEXT NOT NULL,
  velocidad REAL NOT NULL, coste INTEGER NOT NULL, error TEXT,
  creado INTEGER NOT NULL, empezado INTEGER, terminado INTEGER);
CREATE INDEX IF NOT EXISTS ix_trab_estado ON trabajos(estado, creado);
CREATE TABLE IF NOT EXISTS intentos (clave TEXT NOT NULL, momento INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS voces (
  id TEXT PRIMARY KEY, usuario_id INTEGER NOT NULL, nombre TEXT NOT NULL, estado TEXT NOT NULL,
  frase TEXT NOT NULL, archivo TEXT NOT NULL, nota TEXT, creado INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS piezas (
  id TEXT PRIMARY KEY, usuario_id INTEGER NOT NULL, estado TEXT NOT NULL,
  progreso INTEGER NOT NULL DEFAULT 0, fase TEXT, texto TEXT NOT NULL, previo_id TEXT,
  resultado TEXT, coste INTEGER NOT NULL, error TEXT,
  creado INTEGER NOT NULL, empezado INTEGER, terminado INTEGER);
CREATE INDEX IF NOT EXISTS ix_piezas_estado ON piezas(estado, creado);
CREATE TABLE IF NOT EXISTS asistente (
  id TEXT PRIMARY KEY, usuario_id INTEGER NOT NULL, estado TEXT NOT NULL,
  peticion TEXT, respuesta TEXT, creado INTEGER NOT NULL, empezado INTEGER);
"""


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB, timeout=10, isolation_level=None)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys=ON")
        g.db.execute("PRAGMA secure_delete=ON")   # lo borrado se sobrescribe (mensajes del asistente)
    return g.db


@app.teardown_appcontext
def cerrar_db(_):
    c = g.pop("db", None)
    if c:
        c.close()


def iniciar():
    DATOS.mkdir(parents=True, exist_ok=True)
    ARCHIVOS.mkdir(exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(ESQUEMA)
    columnas = {r[1] for r in c.execute("PRAGMA table_info(trabajos)")}
    if "ajustes" not in columnas:          # migración: bases de datos creadas antes de la voz clonada
        c.execute("ALTER TABLE trabajos ADD COLUMN ajustes TEXT")
    if "admin" not in {r[1] for r in c.execute("PRAGMA table_info(usuarios)")}:
        c.execute("ALTER TABLE usuarios ADD COLUMN admin INTEGER NOT NULL DEFAULT 0")
    c.close()


iniciar()

# ───────────────────────── seguridad ─────────────────────────


@app.after_request
def cabeceras(r):
    r.headers["X-Content-Type-Options"] = "nosniff"
    r.headers["X-Frame-Options"] = "DENY"
    r.headers["Referrer-Policy"] = "same-origin"
    r.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' blob: data:; media-src 'self' blob:; "
        "style-src 'self'; script-src 'self'; frame-ancestors 'none'; form-action 'self'")
    return r


def token_csrf():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


app.jinja_env.globals["csrf"] = token_csrf


def comprobar_csrf():
    t = request.form.get("csrf") or request.headers.get("X-CSRF", "")
    if not t or not hmac.compare_digest(t, session.get("csrf", "")):
        abort(400, "Formulario caducado, recarga la página")


def limite(clave, maximo, ventana):
    """Devuelve True si se ha superado el límite de intentos."""
    ahora = int(time.time())
    c = db()
    c.execute("DELETE FROM intentos WHERE momento < ?", (ahora - 3600,))
    n = c.execute("SELECT COUNT(*) FROM intentos WHERE clave=? AND momento>=?",
                  (clave, ahora - ventana)).fetchone()[0]
    if n >= maximo:
        return True
    c.execute("INSERT INTO intentos VALUES (?,?)", (clave, ahora))
    return False


def ip():
    # Con AETH_PROXY=1, ProxyFix ya ha puesto aquí la IP real que ve Caddy (no falsificable por el cliente)
    return request.remote_addr or "?"


def usuario_actual():
    uid = session.get("uid")
    if not uid:
        return None
    u = db().execute("SELECT * FROM usuarios WHERE id=? AND activo=1", (uid,)).fetchone()
    if not u:
        session.clear()
    return u


def requiere_login(f):
    @wraps(f)
    def envoltura(*a, **k):
        g.usuario = usuario_actual()
        if not g.usuario:
            if request.method == "GET" and request.path in VOLVER_OK:
                session["volver"] = request.path
            return redirect(url_for("entrar"))
        return f(*a, **k)
    return envoltura


VOLVER_OK = {"/panel", "/3d", "/asistente"}     # tras entrar, volver a la página que se pidió


def requiere_admin(f):
    @wraps(f)
    def envoltura(*a, **k):
        g.usuario = usuario_actual()
        if not g.usuario:
            return redirect(url_for("entrar"))
        if not g.usuario["admin"]:
            abort(404)                      # para un usuario normal, /admin no existe
        return f(*a, **k)
    return envoltura


def requiere_worker(f):
    @wraps(f)
    def envoltura(*a, **k):
        cab = request.headers.get("Authorization", "")
        if not WORKER_CLAVE or not hmac.compare_digest(cab, "Bearer " + WORKER_CLAVE):
            abort(401)
        return f(*a, **k)
    return envoltura


def carpeta_trabajo(uid, tid):
    return ARCHIVOS / str(int(uid)) / tid

# ───────────────────────── páginas públicas ─────────────────────────


@app.get("/")
def inicio():
    if usuario_actual():
        return redirect(url_for("panel"))
    return render_template("inicio.html")


@app.route("/registro", methods=["GET", "POST"])
def registro():
    error = None
    if request.method == "POST":
        comprobar_csrf()
        codigo = request.form.get("codigo", "").strip().upper()
        nombre = request.form.get("nombre", "").strip()
        clave = request.form.get("clave", "")
        if limite("registro:" + ip(), 10, 3600):
            error = "Demasiados intentos. Prueba dentro de una hora."
        elif not (3 <= len(nombre) <= 24 and nombre.replace("_", "").replace("-", "").isalnum()):
            error = "El nombre debe tener 3–24 letras, números, - o _."
        elif len(clave) < 10:
            error = "La contraseña debe tener al menos 10 caracteres."
        elif clave != request.form.get("clave2", ""):
            error = "Las contraseñas no coinciden."
        elif not request.form.get("acepto"):
            error = "Debes aceptar las condiciones de uso."
        else:
            c = db()
            c.execute("BEGIN IMMEDIATE")
            inv = c.execute("SELECT * FROM invitaciones WHERE codigo=?", (codigo,)).fetchone()
            if (not inv or inv["revocada"] or inv["usado_por"] or inv["caduca"] < time.time()):
                c.execute("ROLLBACK")
                error = "Código de invitación no válido o caducado."
            elif c.execute("SELECT 1 FROM usuarios WHERE nombre=?", (nombre,)).fetchone():
                c.execute("ROLLBACK")
                error = "Ese nombre ya está en uso."
            else:
                cur = c.execute(
                    "INSERT INTO usuarios (nombre, hash, nivel, creditos, origen, creado) "
                    "VALUES (?,?,?,?, 'invitacion', ?)",
                    (nombre, generate_password_hash(clave), inv["nivel"], inv["creditos"], int(time.time())))
                c.execute("UPDATE invitaciones SET usado_por=?, usado_en=? WHERE codigo=?",
                          (cur.lastrowid, int(time.time()), codigo))
                c.execute("COMMIT")
                session.clear()
                session.permanent = True
                session["uid"] = cur.lastrowid
                return redirect(url_for("panel"))
    return render_template("registro.html", error=error, codigo=request.args.get("codigo", ""))


@app.route("/entrar", methods=["GET", "POST"])
def entrar():
    error = None
    if request.method == "POST":
        comprobar_csrf()
        nombre = request.form.get("nombre", "").strip()
        if limite("entrar:" + ip(), 10, 900) or limite("entrar-u:" + nombre.lower(), 10, 900):
            error = "Demasiados intentos. Espera 15 minutos."
        else:
            u = db().execute("SELECT * FROM usuarios WHERE nombre=? AND activo=1", (nombre,)).fetchone()
            if u and check_password_hash(u["hash"], request.form.get("clave", "")):
                volver = session.get("volver")
                session.clear()
                session.permanent = True
                session["uid"] = u["id"]
                return redirect(volver if volver in VOLVER_OK else url_for("panel"))
            error = "Nombre o contraseña incorrectos."
    return render_template("entrar.html", error=error)


@app.post("/salir")
def salir():
    comprobar_csrf()
    session.clear()
    return redirect(url_for("inicio"))


@app.get("/condiciones")
def condiciones():
    return render_template("condiciones.html")

# ───────────────────────── panel del usuario ─────────────────────────


@app.get("/panel")
@requiere_login
def panel():
    trabajos = db().execute(
        "SELECT * FROM trabajos WHERE usuario_id=? ORDER BY creado DESC LIMIT 30",
        (g.usuario["id"],)).fetchall()
    return render_template("panel.html", u=g.usuario, trabajos=trabajos, voces=VOCES,
                           idiomas=IDIOMAS, calidades=CALIDADES, ritmos=RITMOS, niveles=NIVELES,
                           expresividad=EXPRESIVIDAD, max_guion=MAX_GUION, coste_clon=COSTE_CLON)


@app.post("/api/trabajo")
@requiere_login
def crear_trabajo():
    comprobar_csrf()
    u = g.usuario
    f = request.form
    guion = f.get("guion", "").strip()
    voz, idioma, calidad = f.get("voz", ""), f.get("idioma", ""), f.get("calidad", "rapida")
    if f.get("ritmo", "normal") not in RITMOS:
        return jsonify(error="Ritmo no válido."), 400
    velocidad = RITMOS[f.get("ritmo", "normal")][1]
    if not guion or len(guion) > MAX_GUION:
        return jsonify(error=f"El texto debe tener entre 1 y {MAX_GUION} caracteres."), 400
    ajustes = None
    if voz.startswith("clon:"):
        vid = voz[5:]
        v = db().execute("SELECT * FROM voces WHERE id=? AND usuario_id=?", (vid, u["id"])).fetchone() \
            if ID_VOZ.match(vid) else None
        if not v or v["estado"] != "lista":
            return jsonify(error="Esa voz clonada no existe o aún no está aprobada."), 400
        if idioma not in IDIOMAS:
            return jsonify(error="Idioma no válido."), 400
        exp = f.get("expresividad", "normal")
        if exp not in EXPRESIVIDAD:
            return jsonify(error="Expresividad no válida."), 400
        ajustes = json.dumps({"cfg": RITMO_CLON[f.get("ritmo", "normal")], "exageracion": EXPRESIVIDAD[exp][1]})
    elif voz not in VOCES or idioma not in IDIOMAS or VOCES[voz][0] != idioma:
        return jsonify(error="Voz o idioma no válidos."), 400
    if calidad not in CALIDADES:
        return jsonify(error="Calidad no válida."), 400
    if not f.get("consentimiento"):
        return jsonify(error="Debes confirmar que tienes derecho a usar esa imagen."), 400

    # Foto: se abre, se valida y se re-codifica (nunca guardamos el archivo original)
    foto = request.files.get("foto")
    if not foto:
        return jsonify(error="Falta la foto."), 400
    datos = foto.read(MAX_FOTO + 1)
    if len(datos) > MAX_FOTO:
        return jsonify(error="La foto supera 8 MB."), 400
    try:
        Image.MAX_IMAGE_PIXELS = 40_000_000
        with Image.open(io.BytesIO(datos)) as im:
            if im.format not in ("JPEG", "PNG", "WEBP"):
                raise ValueError
            im = ImageOps.exif_transpose(im).convert("RGB")
            if min(im.size) < 256:
                return jsonify(error="La foto es demasiado pequeña (mínimo 256 px)."), 400
            im.thumbnail((1024, 1024), Image.LANCZOS)
            png = io.BytesIO()
            im.save(png, "PNG")
    except Exception:
        return jsonify(error="El archivo no es una imagen JPG, PNG o WEBP válida."), 400

    coste = CALIDADES[calidad][1] + (COSTE_CLON if ajustes else 0)
    tid = time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
    c = db()
    c.execute("BEGIN IMMEDIATE")
    fresco = c.execute("SELECT creditos FROM usuarios WHERE id=?", (u["id"],)).fetchone()
    pendientes = c.execute("SELECT COUNT(*) FROM trabajos WHERE usuario_id=? AND estado IN ('cola','procesando')",
                           (u["id"],)).fetchone()[0]
    if fresco["creditos"] < coste:
        c.execute("ROLLBACK")
        return jsonify(error="No tienes créditos suficientes."), 402
    if pendientes >= MAX_EN_COLA:
        c.execute("ROLLBACK")
        return jsonify(error=f"Ya tienes {MAX_EN_COLA} trabajos en marcha. Espera a que acaben."), 429
    carpeta = carpeta_trabajo(u["id"], tid)
    carpeta.mkdir(parents=True)
    (carpeta / "foto.png").write_bytes(png.getvalue())
    c.execute("UPDATE usuarios SET creditos=creditos-? WHERE id=?", (coste, u["id"]))
    c.execute("INSERT INTO trabajos (id, usuario_id, estado, guion, idioma, voz, calidad, velocidad, coste, creado, ajustes) "
              "VALUES (?,?, 'cola', ?,?,?,?,?,?,?,?)",
              (tid, u["id"], guion, idioma, voz, calidad, velocidad, coste, int(time.time()), ajustes))
    c.execute("COMMIT")
    return jsonify(ok=True, id=tid)


@app.get("/api/trabajos")
@requiere_login
def listar_trabajos():
    c = db()
    filas = c.execute("SELECT id, estado, progreso, fase, error, creado, terminado, calidad, guion "
                      "FROM trabajos WHERE usuario_id=? ORDER BY creado DESC LIMIT 30",
                      (g.usuario["id"],)).fetchall()
    en_cola = c.execute("SELECT id FROM trabajos WHERE estado='cola' ORDER BY creado").fetchall()
    posicion = {r["id"]: i + 1 for i, r in enumerate(en_cola)}
    creditos = c.execute("SELECT creditos FROM usuarios WHERE id=?", (g.usuario["id"],)).fetchone()[0]
    return jsonify(creditos=creditos, trabajos=[
        dict(r, guion=r["guion"][:80], posicion=posicion.get(r["id"])) for r in filas])


def trabajo_propio(tid):
    t = db().execute("SELECT * FROM trabajos WHERE id=? AND usuario_id=?",
                     (tid, g.usuario["id"])).fetchone()
    if not t:
        abort(404)
    return t


@app.get("/archivo/<tid>/<tipo>")
@requiere_login
def archivo(tid, tipo):
    t = trabajo_propio(tid)
    nombre = {"foto": "foto.png", "video": "video.mp4"}.get(tipo)
    ruta = carpeta_trabajo(t["usuario_id"], t["id"]) / (nombre or "x")
    if not nombre or not ruta.is_file():
        abort(404)
    return send_file(ruta, conditional=True, as_attachment=request.args.get("descargar") == "1",
                     download_name=f"aetherion-{t['id']}.{ruta.suffix[1:]}")


@app.post("/api/trabajo/<tid>/borrar")
@requiere_login
def borrar_trabajo(tid):
    comprobar_csrf()
    t = trabajo_propio(tid)
    if t["estado"] in ("cola", "procesando"):
        return jsonify(error="No se puede borrar un trabajo en marcha."), 409
    import shutil
    shutil.rmtree(carpeta_trabajo(t["usuario_id"], t["id"]), ignore_errors=True)
    db().execute("DELETE FROM trabajos WHERE id=?", (t["id"],))
    return jsonify(ok=True)

# ───────────────────────── voces clonadas ─────────────────────────
# Firmas de los formatos de audio admitidos (el worker vuelve a validar con ffmpeg)
FORMATOS_AUDIO = [(b"RIFF", "wav"), (b"OggS", "ogg"), (b"\x1aE\xdf\xa3", "webm"), (b"ID3", "mp3"),
                  (b"\xff\xfb", "mp3"), (b"\xff\xf3", "mp3"), (b"fLaC", "flac")]


def formato_audio(cab):
    for firma, ext in FORMATOS_AUDIO:
        if cab.startswith(firma):
            return ext
    return "m4a" if cab[4:8] == b"ftyp" else None


def carpeta_voces(uid):
    return ARCHIVOS / str(int(uid)) / "voces"


@app.get("/api/voces")
@requiere_login
def listar_voces():
    filas = db().execute("SELECT id, nombre, estado, nota, creado FROM voces WHERE usuario_id=? ORDER BY creado",
                         (g.usuario["id"],)).fetchall()
    return jsonify(voces=[dict(r) for r in filas], max=MAX_VOCES)


@app.get("/api/voces/frase")
@requiere_login
def frase_verificacion():
    """Frase con código aleatorio que el usuario lee al grabar (queda guardada en la sesión)."""
    codigo = "".join(secrets.choice("23456789") for _ in range(4))
    frase = (f"Yo, {g.usuario['nombre']}, doy mi consentimiento para clonar mi voz en Aetherion. "
             f"Código {' '.join(codigo)}.")
    session["frase_voz"] = frase
    return jsonify(frase=frase)


@app.post("/api/voces")
@requiere_login
def crear_voz():
    comprobar_csrf()
    u = g.usuario
    nombre = request.form.get("nombre", "").strip()[:30]
    frase = session.get("frase_voz")
    if not nombre:
        return jsonify(error="Ponle un nombre a la voz."), 400
    if not frase:
        return jsonify(error="Recarga la página para obtener la frase de verificación."), 400
    if not request.form.get("propia"):
        return jsonify(error="Debes confirmar que es tu propia voz."), 400
    c = db()
    if c.execute("SELECT COUNT(*) FROM voces WHERE usuario_id=? AND estado!='rechazada'",
                 (u["id"],)).fetchone()[0] >= MAX_VOCES:
        return jsonify(error=f"Puedes tener como máximo {MAX_VOCES} voces. Borra una antes."), 400
    a = request.files.get("muestra")
    if not a:
        return jsonify(error="Falta la grabación."), 400
    datos = a.read(MAX_MUESTRA + 1)
    if len(datos) > MAX_MUESTRA:
        return jsonify(error="La grabación supera 10 MB."), 400
    if len(datos) < 20_000:
        return jsonify(error="La grabación es demasiado corta. Lee el texto completo con calma."), 400
    ext = formato_audio(datos[:12])
    if not ext:
        return jsonify(error="Formato no admitido (WAV, MP3, OGG, M4A, FLAC o graba aquí)."), 400
    if limite("voz:" + str(u["id"]), 5, 86400):
        return jsonify(error="Has subido demasiadas muestras hoy. Prueba mañana."), 429
    vid = "v" + uuid.uuid4().hex[:12]
    carpeta = carpeta_voces(u["id"])
    carpeta.mkdir(parents=True, exist_ok=True)
    (carpeta / f"{vid}.{ext}").write_bytes(datos)
    estado = "pendiente" if APROBAR_VOCES else "lista"
    c.execute("INSERT INTO voces (id, usuario_id, nombre, estado, frase, archivo, creado) VALUES (?,?,?,?,?,?,?)",
              (vid, u["id"], nombre, estado, frase, f"{vid}.{ext}", int(time.time())))
    session.pop("frase_voz", None)
    return jsonify(ok=True, id=vid, estado=estado)


def voz_propia(vid):
    v = db().execute("SELECT * FROM voces WHERE id=? AND usuario_id=?", (vid, g.usuario["id"])).fetchone() \
        if ID_VOZ.match(vid) else None
    if not v:
        abort(404)
    return v


@app.get("/api/voces/<vid>/muestra")
@requiere_login
def escuchar_voz(vid):
    v = voz_propia(vid)
    return send_file(carpeta_voces(v["usuario_id"]) / v["archivo"], conditional=True)


@app.post("/api/voces/<vid>/borrar")
@requiere_login
def borrar_voz(vid):
    comprobar_csrf()
    v = voz_propia(vid)
    if db().execute("SELECT 1 FROM trabajos WHERE usuario_id=? AND voz=? AND estado IN ('cola','procesando')",
                    (v["usuario_id"], "clon:" + v["id"])).fetchone():
        return jsonify(error="Hay un trabajo en marcha con esta voz."), 409
    (carpeta_voces(v["usuario_id"]) / v["archivo"]).unlink(missing_ok=True)
    db().execute("DELETE FROM voces WHERE id=?", (v["id"],))
    return jsonify(ok=True)

# ───────────────────────── Aetherion 3D ─────────────────────────
from filtro import peticion_prohibida  # noqa: E402

COSTE_3D = int(os.environ.get("AETH_COSTE_3D", "1"))
MAX_TEXTO_3D = 800
MAX_STL = 60 * 1024 * 1024
ARCHIVOS_3D = {"stl": ("modelo.stl", "model/stl"), "png": ("vista.png", "image/png"),
               "scad": ("modelo.scad", "text/plain")}


def carpeta_pieza(uid, pid):
    return ARCHIVOS / str(int(uid)) / "3d" / pid


@app.get("/3d")
@requiere_login
def pagina_3d():
    return render_template("3d.html", u=g.usuario, niveles=NIVELES, coste=COSTE_3D, max_texto=MAX_TEXTO_3D)


@app.post("/api/3d")
@requiere_login
def crear_pieza():
    comprobar_csrf()
    u = g.usuario
    texto = request.form.get("texto", "").strip()
    previo = request.form.get("previo", "").strip() or None
    if not texto or len(texto) > MAX_TEXTO_3D:
        return jsonify(error=f"Describe el objeto (máximo {MAX_TEXTO_3D} caracteres)."), 400
    if peticion_prohibida(texto):
        return jsonify(error="Esa petición no está permitida: no diseñamos armas ni piezas peligrosas."), 400
    c = db()
    if previo:
        pp = c.execute("SELECT * FROM piezas WHERE id=? AND usuario_id=? AND estado='hecho'",
                       (previo, u["id"])).fetchone()
        if not pp:
            return jsonify(error="La pieza que quieres retocar no existe o no está terminada."), 400
    pid = time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:8]
    c.execute("BEGIN IMMEDIATE")
    cred = c.execute("SELECT creditos FROM usuarios WHERE id=?", (u["id"],)).fetchone()[0]
    pendientes = c.execute("SELECT (SELECT COUNT(*) FROM piezas WHERE usuario_id=? AND estado IN ('cola','procesando'))"
                           " + (SELECT COUNT(*) FROM trabajos WHERE usuario_id=? AND estado IN ('cola','procesando'))",
                           (u["id"], u["id"])).fetchone()[0]
    if cred < COSTE_3D:
        c.execute("ROLLBACK")
        return jsonify(error="No tienes créditos suficientes."), 402
    if pendientes >= MAX_EN_COLA:
        c.execute("ROLLBACK")
        return jsonify(error=f"Ya tienes {MAX_EN_COLA} trabajos en marcha. Espera a que acaben."), 429
    c.execute("UPDATE usuarios SET creditos=creditos-? WHERE id=?", (COSTE_3D, u["id"]))
    c.execute("INSERT INTO piezas (id, usuario_id, estado, texto, previo_id, coste, creado) VALUES (?,?, 'cola', ?,?,?,?)",
              (pid, u["id"], texto, previo, COSTE_3D, int(time.time())))
    c.execute("COMMIT")
    return jsonify(ok=True, id=pid)


@app.get("/api/3d")
@requiere_login
def listar_piezas():
    c = db()
    filas = c.execute("SELECT id, estado, progreso, fase, texto, previo_id, resultado, error, creado, terminado "
                      "FROM piezas WHERE usuario_id=? ORDER BY creado DESC LIMIT 40", (g.usuario["id"],)).fetchall()
    cola = [r[0] for r in c.execute("SELECT creado FROM piezas WHERE estado='cola' UNION ALL "
                                    "SELECT creado FROM trabajos WHERE estado='cola' ORDER BY 1")]
    creditos = c.execute("SELECT creditos FROM usuarios WHERE id=?", (g.usuario["id"],)).fetchone()[0]
    salida = []
    for r in filas:
        d = dict(r)
        d["resultado"] = json.loads(r["resultado"]) if r["resultado"] else None
        d["posicion"] = (sum(1 for x in cola if x < r["creado"]) + 1) if r["estado"] == "cola" else None
        salida.append(d)
    return jsonify(creditos=creditos, piezas=salida)


def pieza_propia(pid):
    p = db().execute("SELECT * FROM piezas WHERE id=? AND usuario_id=?", (pid, g.usuario["id"])).fetchone()
    if not p:
        abort(404)
    return p


@app.get("/archivo3d/<pid>/<tipo>")
@requiere_login
def archivo_3d(pid, tipo):
    p = pieza_propia(pid)
    if tipo not in ARCHIVOS_3D:
        abort(404)
    nombre, mime = ARCHIVOS_3D[tipo]
    ruta = carpeta_pieza(p["usuario_id"], p["id"]) / nombre
    if not ruta.is_file():
        abort(404)
    return send_file(ruta, mimetype=mime, conditional=True, as_attachment=request.args.get("descargar") == "1",
                     download_name=f"aetherion-3d-{p['id']}.{tipo}")


@app.post("/api/3d/<pid>/borrar")
@requiere_login
def borrar_pieza(pid):
    comprobar_csrf()
    p = pieza_propia(pid)
    if p["estado"] in ("cola", "procesando"):
        return jsonify(error="No se puede borrar una pieza en marcha."), 409
    import shutil
    shutil.rmtree(carpeta_pieza(p["usuario_id"], p["id"]), ignore_errors=True)
    db().execute("DELETE FROM piezas WHERE id=?", (p["id"],))
    return jsonify(ok=True)


def descripcion_pieza(c, pid, nivel=0):
    """Descripción acumulada de una pieza: la petición inicial más los retoques que se le han hecho."""
    p = c.execute("SELECT texto, previo_id FROM piezas WHERE id=?", (pid,)).fetchone()
    if not p:
        return ""
    if p["previo_id"] and nivel < 6:
        base = descripcion_pieza(c, p["previo_id"], nivel + 1)
        return (base + "\nCambio hecho después: " + p["texto"]) if base else p["texto"]
    return p["texto"]


def pieza_worker(pid):
    p = db().execute("SELECT * FROM piezas WHERE id=? AND estado='procesando'", (pid,)).fetchone()
    if not p:
        abort(404)
    return p


@app.get("/worker/3d/<pid>/previo")
@requiere_worker
def worker_3d_previo(pid):
    p = pieza_worker(pid)
    if not p["previo_id"]:
        abort(404)
    ruta = carpeta_pieza(p["usuario_id"], p["previo_id"]) / "modelo.scad"
    if not ruta.is_file():
        abort(404)
    return send_file(ruta, mimetype="text/plain")


@app.post("/worker/3d/<pid>/progreso")
@requiere_worker
def worker_3d_progreso(pid):
    pieza_worker(pid)
    d = request.get_json(silent=True) or {}
    db().execute("UPDATE piezas SET progreso=?, fase=? WHERE id=?",
                 (max(1, min(99, int(d.get("progreso", 1)))), str(d.get("fase", ""))[:60], pid))
    return jsonify(ok=True)


@app.post("/worker/3d/<pid>/resultado")
@requiere_worker
def worker_3d_resultado(pid):
    p = pieza_worker(pid)
    stl, scad, png = (request.files.get(k) for k in ("stl", "scad", "png"))
    if not stl or not scad:
        abort(400)
    try:
        info = json.loads(request.form.get("resultado", "{}"))
        info = {k: info[k] for k in ("medidas_mm", "triangulos", "volumen_cm3", "cerrado", "avisos", "intentos", "piezas", "llenado")
                if k in info}
    except Exception:
        info = {}
    carpeta = carpeta_pieza(p["usuario_id"], p["id"])
    carpeta.mkdir(parents=True, exist_ok=True)
    datos_stl = stl.read(MAX_STL + 1)
    if len(datos_stl) > MAX_STL or len(datos_stl) < 84:
        abort(400, "STL no válido")
    (carpeta / "modelo.stl").write_bytes(datos_stl)
    (carpeta / "modelo.scad").write_bytes(scad.read(100_000))
    if png:
        datos_png = png.read(5 * 1024 * 1024)
        if datos_png[:8] == b"\x89PNG\r\n\x1a\n":
            (carpeta / "vista.png").write_bytes(datos_png)
    db().execute("UPDATE piezas SET estado='hecho', progreso=100, fase='Lista', resultado=?, terminado=? WHERE id=?",
                 (json.dumps(info, ensure_ascii=False), int(time.time()), pid))
    return jsonify(ok=True)


@app.post("/worker/3d/<pid>/error")
@requiere_worker
def worker_3d_error(pid):
    p = pieza_worker(pid)
    d = request.get_json(silent=True) or {}
    c = db()
    c.execute("BEGIN IMMEDIATE")
    c.execute("UPDATE piezas SET estado='error', fase='Error', error=?, terminado=? WHERE id=?",
              (str(d.get("error", "Error en el servidor"))[:300], int(time.time()), pid))
    c.execute("UPDATE usuarios SET creditos=creditos+? WHERE id=?", (p["coste"], p["usuario_id"]))
    c.execute("COMMIT")
    return jsonify(ok=True)

# ───────────────────────── administración ─────────────────────────
LETRAS_CODIGO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # sin 0/O ni 1/I


def nuevo_codigo():
    bloque = lambda: "".join(secrets.choice(LETRAS_CODIGO) for _ in range(4))  # noqa: E731
    return f"AETH-{bloque()}-{bloque()}-{bloque()}"


def aviso(texto):
    session.setdefault("avisos", []).append(texto)
    session.modified = True


def volver_admin(ancla=""):
    return redirect(url_for("admin") + ("#" + ancla if ancla else ""))


@app.get("/admin")
@requiere_admin
def admin():
    c = db()
    voces = c.execute("SELECT v.*, u.nombre AS usuario FROM voces v JOIN usuarios u ON u.id=v.usuario_id "
                      "ORDER BY v.estado!='pendiente', v.creado DESC LIMIT 100").fetchall()
    invitaciones = c.execute("SELECT i.*, u.nombre AS usuario FROM invitaciones i "
                             "LEFT JOIN usuarios u ON u.id=i.usado_por ORDER BY i.creado DESC LIMIT 100").fetchall()
    usuarios = c.execute("SELECT u.*, (SELECT COUNT(*) FROM trabajos t WHERE t.usuario_id=u.id) AS n_trabajos "
                         "FROM usuarios u ORDER BY u.creado").fetchall()
    cola = c.execute("SELECT t.id, t.estado, t.progreso, t.fase, t.calidad, t.voz, t.creado, u.nombre AS usuario "
                     "FROM trabajos t JOIN usuarios u ON u.id=t.usuario_id "
                     "WHERE t.estado IN ('cola','procesando') ORDER BY t.creado").fetchall()
    stats = c.execute("SELECT COUNT(*) AS total, SUM(estado='hecho') AS hechos, SUM(estado='error') AS errores "
                      "FROM trabajos").fetchone()
    avisos = session.pop("avisos", [])
    return render_template("admin.html", u=g.usuario, voces=voces, invitaciones=invitaciones, usuarios=usuarios,
                           cola=cola, stats=stats, avisos=avisos, niveles=NIVELES, ahora=int(time.time()),
                           fecha=lambda t: time.strftime("%d/%m/%Y %H:%M", time.localtime(t)) if t else "—")


@app.get("/admin/voz/<vid>/muestra")
@requiere_admin
def admin_muestra(vid):
    v = db().execute("SELECT * FROM voces WHERE id=?", (vid,)).fetchone() if ID_VOZ.match(vid) else None
    if not v:
        abort(404)
    return send_file(carpeta_voces(v["usuario_id"]) / v["archivo"], conditional=True)


@app.post("/admin/voz/<vid>/<accion>")
@requiere_admin
def admin_voz(vid, accion):
    comprobar_csrf()
    if accion not in ("aprobar", "rechazar") or not ID_VOZ.match(vid):
        abort(400)
    estado = "lista" if accion == "aprobar" else "rechazada"
    nota = None if estado == "lista" else (request.form.get("motivo", "").strip()[:120] or "La grabación no es válida")
    n = db().execute("UPDATE voces SET estado=?, nota=? WHERE id=?", (estado, nota, vid)).rowcount
    aviso("Voz aprobada." if n and estado == "lista" else "Voz rechazada." if n else "Esa voz no existe.")
    return volver_admin("voces")


@app.post("/admin/invitar")
@requiere_admin
def admin_invitar():
    comprobar_csrf()
    f = request.form
    nivel = f.get("nivel")
    try:
        creditos = max(0, min(1000, int(f.get("creditos", 10))))
        dias = max(1, min(365, int(f.get("dias", 14))))
        cantidad = max(1, min(20, int(f.get("cantidad", 1))))
    except ValueError:
        abort(400)
    if nivel not in NIVELES:
        abort(400)
    ahora = int(time.time())
    c = db()
    codigos = []
    for _ in range(cantidad):
        cod = nuevo_codigo()
        c.execute("INSERT INTO invitaciones (codigo, nivel, creditos, caduca, nota, creado) VALUES (?,?,?,?,?,?)",
                  (cod, nivel, creditos, ahora + dias * 86400, f.get("nota", "").strip()[:60], ahora))
        codigos.append(cod)
    aviso("Invitaciones creadas: " + "  ".join(codigos))
    return volver_admin("invitaciones")


@app.post("/admin/invitacion/<codigo>/revocar")
@requiere_admin
def admin_revocar(codigo):
    comprobar_csrf()
    n = db().execute("UPDATE invitaciones SET revocada=1 WHERE codigo=? AND usado_por IS NULL", (codigo,)).rowcount
    aviso("Invitación revocada." if n else "No se puede revocar (ya usada o no existe).")
    return volver_admin("invitaciones")


@app.post("/admin/usuario/<int:uid>/<accion>")
@requiere_admin
def admin_usuario(uid, accion):
    comprobar_csrf()
    c = db()
    if accion == "creditos":
        try:
            cambio = max(-1000, min(1000, int(request.form.get("cambio", 0))))
        except ValueError:
            abort(400)
        c.execute("UPDATE usuarios SET creditos=MAX(0, creditos+?) WHERE id=?", (cambio, uid))
        aviso(f"Créditos {'+' if cambio >= 0 else ''}{cambio}.")
    elif accion == "nivel" and request.form.get("nivel") in NIVELES:
        c.execute("UPDATE usuarios SET nivel=? WHERE id=?", (request.form["nivel"], uid))
        aviso("Nivel cambiado.")
    elif accion in ("bloquear", "desbloquear"):
        if uid == g.usuario["id"]:
            aviso("No puedes bloquear tu propia cuenta.")
        else:
            c.execute("UPDATE usuarios SET activo=? WHERE id=?", (int(accion == "desbloquear"), uid))
            aviso("Usuario " + ("bloqueado." if accion == "bloquear" else "desbloqueado."))
    else:
        abort(400)
    return volver_admin("usuarios")


# ───────────────────────── Aetherion Asistente ─────────────────────────
# Local-first: notas, recordatorios, archivos de datos e historial viven en el MÓVIL del usuario.
# La web solo hace de buzón: guarda cada petición unos segundos mientras el worker la responde y la
# borra en cuanto el móvil recoge la respuesta (secure_delete sobrescribe lo borrado).
COSTE_ASISTENTE = 0            # gratis durante las pruebas
MAX_PETICION_ASIST = 48 * 1024
MAX_TEXTO_ASIST = 2000
MAX_MEMORIA_ASIST = 16000
VIDA_ASIST = 120               # s: lo que no se recoge en este tiempo se borra
ID_ASIST = re.compile(r"^a[0-9a-f]{20}$")
FECHA_LOCAL = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")


def purgar_asistente(c):
    c.execute("DELETE FROM asistente WHERE creado < ?", (int(time.time()) - VIDA_ASIST,))


def olvidar_asistente(c, aid):
    """Borra el mensaje y vuelca el diario WAL para que no quede ni rastro en disco."""
    c.execute("DELETE FROM asistente WHERE id=?", (aid,))
    try:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error:
        pass


@app.get("/asistente")
@requiere_login
def pagina_asistente():
    return render_template("asistente.html", u=g.usuario)


@app.get("/asistente/sw.js")
def asistente_sw():
    r = make_response(send_file(Path(app.static_folder) / "asistente" / "sw.js", mimetype="text/javascript",
                                max_age=0))
    r.headers["Service-Worker-Allowed"] = "/asistente"
    r.headers["Cache-Control"] = "no-cache"
    return r


@app.get("/asistente/manifest.webmanifest")
def asistente_manifest():
    return send_file(Path(app.static_folder) / "asistente" / "manifest.webmanifest",
                     mimetype="application/manifest+json", max_age=3600)


def texto_limpio(v, maximo):
    return str(v if v is not None else "").replace("\x00", "")[:maximo]


@app.post("/api/asistente")
@requiere_login
def asistente_preguntar():
    comprobar_csrf()
    u = g.usuario
    if request.content_length and request.content_length > MAX_PETICION_ASIST:
        return jsonify(error="Mensaje demasiado grande."), 413
    d = request.get_json(silent=True) or {}
    texto = texto_limpio(d.get("texto"), MAX_TEXTO_ASIST + 1).strip()
    if not texto or len(texto) > MAX_TEXTO_ASIST:
        return jsonify(error=f"Escribe algo (máximo {MAX_TEXTO_ASIST} caracteres)."), 400
    ahora_local = str(d.get("ahora", ""))
    if not FECHA_LOCAL.match(ahora_local):
        return jsonify(error="Falta la fecha del móvil."), 400
    historial = []
    for m in (d.get("historial") or [])[-12:]:
        if isinstance(m, dict) and m.get("rol") in ("yo", "aether"):
            historial.append({"rol": m["rol"], "texto": texto_limpio(m.get("texto"), 1500)})
    peticion = {"texto": texto, "ahora": ahora_local, "dia": texto_limpio(d.get("dia"), 20),
                "memoria": texto_limpio(d.get("memoria"), MAX_MEMORIA_ASIST), "historial": historial}
    if limite("asist-min:" + str(u["id"]), 20, 60) or limite("asist-dia:" + str(u["id"]), 400, 86400):
        return jsonify(error="Vas muy rápido: espera un momento."), 429
    c = db()
    purgar_asistente(c)
    if c.execute("SELECT COUNT(*) FROM asistente WHERE usuario_id=?", (u["id"],)).fetchone()[0] >= 3:
        return jsonify(error="Espera a que termine la respuesta anterior."), 429
    aid = "a" + secrets.token_hex(10)
    c.execute("INSERT INTO asistente (id, usuario_id, estado, peticion, creado) VALUES (?,?, 'cola', ?, ?)",
              (aid, u["id"], json.dumps(peticion, ensure_ascii=False), int(time.time())))
    return jsonify(ok=True, id=aid)


@app.get("/api/asistente/<aid>")
@requiere_login
def asistente_respuesta(aid):
    if not ID_ASIST.match(aid):
        abort(404)
    c = db()
    r = c.execute("SELECT * FROM asistente WHERE id=? AND usuario_id=?", (aid, g.usuario["id"])).fetchone()
    if not r:
        return jsonify(estado="perdido", error="La respuesta ha caducado. Vuelve a preguntar."), 404
    edad = int(time.time()) - r["creado"]
    if r["estado"] in ("hecho", "error"):
        olvidar_asistente(c, aid)                                    # entregada → fuera del servidor
        datos = json.loads(r["respuesta"] or "{}")
        if r["estado"] == "error":
            return jsonify(estado="error", error=datos.get("error", "No he podido responder."))
        return jsonify(estado="hecho", **datos)
    if r["estado"] == "cola" and edad > 45:
        olvidar_asistente(c, aid)
        return jsonify(estado="error", error="El motor de Aether no está disponible ahora mismo. Inténtalo más tarde.")
    return jsonify(estado=r["estado"])


@app.post("/worker/asistente/siguiente")
@requiere_worker
def worker_asistente_siguiente():
    """Espera (hasta ~20 s) una pregunta del asistente y la entrega."""
    fin = time.time() + 20
    c = db()
    while True:
        ahora = int(time.time())
        c.execute("BEGIN IMMEDIATE")
        purgar_asistente(c)
        r = c.execute("SELECT * FROM asistente WHERE estado='cola' AND creado >= ? ORDER BY creado LIMIT 1",
                      (ahora - 45,)).fetchone()
        if r:
            c.execute("UPDATE asistente SET estado='procesando', empezado=?, peticion=NULL WHERE id=?",
                      (ahora, r["id"]))
            c.execute("COMMIT")
            return jsonify(id=r["id"], **json.loads(r["peticion"]))
        c.execute("COMMIT")
        if time.time() > fin:
            return "", 204
        time.sleep(0.4)


TIPOS_ACCION = {"nota", "recordatorio", "dato", "calculo", "borrar_nota", "borrar_recordatorio"}


def acciones_validas(lista):
    salida = []
    for a in (lista if isinstance(lista, list) else [])[:8]:
        if not isinstance(a, dict) or a.get("tipo") not in TIPOS_ACCION:
            continue
        limpia = {"tipo": a["tipo"]}
        for campo, maximo in (("texto", 1000), ("fecha", 16), ("archivo", 60), ("contenido", 2000),
                              ("expresion", 200), ("id", 40)):
            if a.get(campo) not in (None, ""):
                limpia[campo] = texto_limpio(a[campo], maximo)
        salida.append(limpia)
    return salida


@app.post("/worker/asistente/<aid>/<accion>")
@requiere_worker
def worker_asistente_fin(aid, accion):
    if not ID_ASIST.match(aid) or accion not in ("resultado", "error"):
        abort(404)
    c = db()
    if not c.execute("SELECT 1 FROM asistente WHERE id=? AND estado='procesando'", (aid,)).fetchone():
        abort(404)
    d = request.get_json(silent=True) or {}
    if accion == "error":
        datos, estado = {"error": texto_limpio(d.get("error") or "No he podido responder.", 300)}, "error"
    else:
        datos = {"respuesta": texto_limpio(d.get("respuesta"), 4000), "acciones": acciones_validas(d.get("acciones"))}
        estado = "hecho"
    c.execute("UPDATE asistente SET estado=?, respuesta=? WHERE id=?", (estado, json.dumps(datos, ensure_ascii=False), aid))
    return jsonify(ok=True)

# ───────────────────────── API del worker ─────────────────────────


@app.get("/worker/ping")
@requiere_worker
def worker_ping():
    """Comprueba la clave del worker sin tocar la cola."""
    return jsonify(ok=True)


@app.post("/worker/siguiente")
@requiere_worker
def worker_siguiente():
    """Entrega el trabajo más antiguo de la cola y lo marca como 'procesando'."""
    ahora = int(time.time())
    c = db()
    c.execute("BEGIN IMMEDIATE")
    # Trabajos abandonados (worker apagado a mitad): vuelven a la cola
    for tabla in ("trabajos", "piezas"):
        c.execute(f"UPDATE {tabla} SET estado='cola', progreso=0, fase=NULL "
                  "WHERE estado='procesando' AND empezado < ?", (ahora - CADUCA_PROCESANDO,))
    t = c.execute("SELECT * FROM trabajos WHERE estado='cola' ORDER BY creado LIMIT 1").fetchone()
    p = c.execute("SELECT * FROM piezas WHERE estado='cola' ORDER BY creado LIMIT 1").fetchone()
    if p and (not t or p["creado"] < t["creado"]):          # el más antiguo de las dos colas
        c.execute("UPDATE piezas SET estado='procesando', empezado=?, progreso=1, fase='Empezando' WHERE id=?",
                  (ahora, p["id"]))
        c.execute("COMMIT")
        return jsonify(tipo="3d", id=p["id"], texto=p["texto"], previo=bool(p["previo_id"]),
                       original=descripcion_pieza(c, p["previo_id"]) if p["previo_id"] else "")
    if not t:
        c.execute("COMMIT")
        return "", 204
    c.execute("UPDATE trabajos SET estado='procesando', empezado=?, progreso=1, fase='Empezando' WHERE id=?",
              (ahora, t["id"]))
    c.execute("COMMIT")
    return jsonify(tipo="avatar", id=t["id"], guion=t["guion"], idioma=t["idioma"], voz=t["voz"],
                   calidad=t["calidad"], velocidad=t["velocidad"], ajustes=json.loads(t["ajustes"] or "{}"))


def trabajo_worker(tid):
    t = db().execute("SELECT * FROM trabajos WHERE id=? AND estado='procesando'", (tid,)).fetchone()
    if not t:
        abort(404)
    return t


@app.get("/worker/foto/<tid>")
@requiere_worker
def worker_foto(tid):
    t = trabajo_worker(tid)
    return send_file(carpeta_trabajo(t["usuario_id"], t["id"]) / "foto.png", mimetype="image/png")


@app.get("/worker/muestra/<tid>")
@requiere_worker
def worker_muestra(tid):
    """Muestra de la voz clonada que usa el trabajo (solo si es del mismo usuario y está aprobada)."""
    t = trabajo_worker(tid)
    if not t["voz"].startswith("clon:"):
        abort(404)
    v = db().execute("SELECT * FROM voces WHERE id=? AND usuario_id=? AND estado='lista'",
                     (t["voz"][5:], t["usuario_id"])).fetchone()
    if not v:
        abort(404)
    return send_file(carpeta_voces(v["usuario_id"]) / v["archivo"], mimetype="application/octet-stream")


@app.post("/worker/progreso/<tid>")
@requiere_worker
def worker_progreso(tid):
    trabajo_worker(tid)
    d = request.get_json(silent=True) or {}
    p = max(1, min(99, int(d.get("progreso", 1))))
    fase = str(d.get("fase", ""))[:60]
    db().execute("UPDATE trabajos SET progreso=?, fase=? WHERE id=?", (p, fase, tid))
    return jsonify(ok=True)


@app.post("/worker/resultado/<tid>")
@requiere_worker
def worker_resultado(tid):
    t = trabajo_worker(tid)
    v = request.files.get("video")
    if not v:
        abort(400)
    destino = carpeta_trabajo(t["usuario_id"], t["id"]) / "video.mp4"
    tmp = destino.with_suffix(".parcial")
    v.save(tmp)
    with open(tmp, "rb") as fh:
        cab = fh.read(12)
    if tmp.stat().st_size > MAX_VIDEO or cab[4:8] != b"ftyp":
        tmp.unlink(missing_ok=True)
        abort(400, "No es un MP4")
    tmp.replace(destino)
    db().execute("UPDATE trabajos SET estado='hecho', progreso=100, fase='Listo', terminado=? WHERE id=?",
                 (int(time.time()), tid))
    return jsonify(ok=True)


@app.post("/worker/error/<tid>")
@requiere_worker
def worker_error(tid):
    t = trabajo_worker(tid)
    d = request.get_json(silent=True) or {}
    c = db()
    c.execute("BEGIN IMMEDIATE")
    c.execute("UPDATE trabajos SET estado='error', fase='Error', error=?, terminado=? WHERE id=?",
              (str(d.get("error", "Error en el servidor"))[:300], int(time.time()), tid))
    c.execute("UPDATE usuarios SET creditos=creditos+? WHERE id=?", (t["coste"], t["usuario_id"]))  # devolver
    c.execute("COMMIT")
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PUERTO", "8100")), debug=False)
