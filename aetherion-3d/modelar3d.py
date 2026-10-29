#!/usr/bin/env python3
"""Aetherion 3D — de texto a pieza imprimible (STL) — se ejecuta en el X99.

  modelar3d.py --texto "soporte de pared para móvil, 80 mm de ancho" --salida /ruta/carpeta
  modelar3d.py --previo /ruta/modelo.scad --texto "hazlo 2 cm más alto" --salida /ruta/carpeta

1. Una IA local (llama.cpp, API compatible OpenAI) escribe código OpenSCAD paramétrico.
2. OpenSCAD lo construye en un contenedor SIN red, de solo lectura y con límites de tiempo/memoria.
3. Si OpenSCAD da error, se le devuelve el error a la IA para que lo corrija (máx. 2 veces).
4. Comprobaciones: medidas, que quepa en la cama, sólido cerrado (estanco).
Salida en la carpeta: modelo.scad, modelo.stl, vista.png, resultado.json
"""
import argparse
import json
import os
import re
import struct
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

LLM = os.environ.get("AETH_LLM_3D", "http://127.0.0.1:8081")       # IA oficial de las webs
IMAGEN_SCAD = os.environ.get("AETH_IMAGEN_SCAD", "aetherion-openscad")
CAMA = (220.0, 220.0, 250.0)        # Ender-3 S1 Pro (margen respecto a 270 de alto)
MAX_CODIGO = 20_000
MAX_TEXTO = 800
REINTENTOS = 2
VISION = os.environ.get("AETH_3D_VISION", "1") == "1"   # la IA revisa la imagen de su pieza

# ───────────────────────── filtro de peticiones ─────────────────────────
# Armas y sus piezas: rechazo antes de llegar a la IA (fabricarlas es delito en España).
PROHIBIDO = [
    r"\barma(s)?\b", r"\bpistola", r"\brevolver", r"\bfusil", r"\brifle", r"\bescopeta", r"\bsubfusil",
    r"\bgatillo", r"\bcargador(es)? de (arma|pistola|fusil|balas)", r"\bsilenciador", r"\bsupresor",
    r"\bmunici[oó]n", r"\bbalas?\b", r"\bcartucho(s)? de (bala|escopeta)", r"\brecamara\b", r"\bc[aá]mara de disparo",
    r"\bgun\b", r"\bfirearm", r"\bpistol\b", r"\brifle\b", r"\bshotgun", r"\btrigger\b", r"\bsuppressor",
    r"\bsilencer", r"\bsolvent trap", r"\bammo\b", r"\bammunition", r"\bbullet", r"\breceiver\b",
    r"\bauto ?sear", r"\bbump ?stock", r"\bglock", r"\bar-?15\b", r"\bak-?47\b", r"\bfgc-?9\b", r"\bghost gun",
    r"\bnudillera", r"\bpu[nñ]o americano", r"\bbrass knuckle", r"\bexplosiv", r"\bgranada\b", r"\bgrenade",
]


def normalizar(t):
    t = unicodedata.normalize("NFKD", t.lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def peticion_prohibida(texto):
    n = normalizar(texto)
    return any(re.search(normalizar(p), n) for p in PROHIBIDO)


# ───────────────────────── IA ─────────────────────────
SISTEMA = """Eres un ingeniero de producto experto en OpenSCAD y en impresión 3D FDM (PLA, boquilla 0,4 mm).
Diseñas piezas FUNCIONALES: antes de dibujar piensas cómo se usa el objeto, qué partes necesita y cómo se imprime.

Reglas obligatorias:
- Unidades en milímetros. Cama de 220 x 220 mm, altura máxima 250 mm.
- Al principio, todas las medidas como variables con nombre en español y un comentario corto (diseño paramétrico).
- La pieza se apoya en z = 0 sobre su cara más grande y plana (la que mejor se imprime sin soportes).
- La cara "frontal" del objeto (la que se ve cuando se usa) hacia +Y.
- PIEZAS LIGERAS: nunca un bloque macizo. Usa paredes de 2–3 mm, huecos, nervios y vaciados; un objeto
  de uso diario rara vez ocupa más del 40 % de su caja envolvente.
- Paredes de al menos 1,6 mm; detalles de al menos 0,8 mm; agujeros para tornillo con 0,3 mm de holgura.
- Evita voladizos de más de 45° y puentes largos; usa chaflanes en lugar de salientes horizontales.
- $fn = 64 para curvas (máximo 96).
- Prohibido: import(), include, use, surface(), minkowski() y hull() con muchos objetos (son lentos).
- RECIPIENTES (maceta, caja, vaso, cuenco, bandeja, organizador): SIEMPRE abiertos hacia +Z (la boca arriba),
  con el fondo apoyado en la cama. Nunca boca abajo.
- VARIAS PIEZAS (tapa, plato, "separado", "dos piezas", "kit"): cada pieza apoyada en z = 0 y colocadas UNA AL LADO
  DE LA OTRA en X con 10 mm de separación. Nunca una encima o dentro de otra. Si no se piden, un solo objeto.
- Texto solo si se pide (text() con font "Liberation Sans:style=Bold").
- Comprueba la geometría: usa funciones simples (sin fórmulas trigonométricas inventadas).
- Nunca diseñes armas, piezas de armas, munición ni nada para hacer daño: en ese caso responde solo: RECHAZADO

Ejemplo de buen estilo (soporte de pared para móvil: placa trasera con tornillos avellanados, cubeta con
base, frente y laterales finos, y ranura para el cable):
```openscad
// Soporte de pared para móvil — ejemplo de pieza funcional y ligera
ancho_movil = 75;      // ancho del móvil
grosor_movil = 12;     // grosor del móvil con funda
pared = 2.4;           // grosor de las paredes
alto_placa = 90;       // altura de la placa trasera
alto_frontal = 25;     // altura del frente que sujeta el móvil
ranura_cable = 15;     // ancho del hueco para el cable
tornillo = 4.3;        // agujero para tornillo M4 (con holgura)
cabeza = 8.5;          // avellanado de la cabeza
$fn = 64;

ancho = ancho_movil + 2 * pared;
fondo = grosor_movil + 2 * pared;

difference() {
  union() {
    cube([ancho, pared, alto_placa]);                       // placa trasera (va contra la pared)
    cube([ancho, fondo, pared]);                            // base donde apoya el móvil
    translate([0, fondo - pared, 0]) cube([ancho, pared, alto_frontal]);   // frente
    for (x = [0, ancho - pared]) translate([x, 0, 0]) cube([pared, fondo, alto_frontal]); // laterales
  }
  // ranura del cable en la base y en el frente
  translate([(ancho - ranura_cable) / 2, pared, -1]) cube([ranura_cable, fondo, pared + 2]);
  translate([(ancho - ranura_cable) / 2, fondo - pared - 1, -1]) cube([ranura_cable, pared + 2, alto_frontal * 0.6]);
  // dos agujeros avellanados para fijar a la pared
  for (z = [alto_placa * 0.45, alto_placa * 0.85])
    translate([ancho / 2, -1, z]) rotate([-90, 0, 0]) {
      cylinder(h = pared + 2, d = tornillo);
      translate([0, 0, pared - 1.6 + 1]) cylinder(h = 1.7, d1 = tornillo, d2 = cabeza);
    }
}
```

Ejemplo de recipiente abierto con una segunda pieza al lado (maceta + plato):
```openscad
// Maceta redonda con plato aparte — ejemplo de recipiente ABIERTO y de varias piezas
diametro = 90;        // diámetro exterior de la maceta
alto = 80;            // altura de la maceta
pared = 2.4;          // grosor de las paredes
fondo = 3;            // grosor del fondo
drenaje = 8;          // agujero de drenaje
alto_plato = 12;      // altura del plato
separacion = 10;      // hueco entre piezas en la cama
$fn = 96;

// Pieza 1: maceta, abierta hacia +Z, fondo apoyado en la cama
module maceta() {
  difference() {
    cylinder(h = alto, d = diametro);
    translate([0, 0, fondo]) cylinder(h = alto, d = diametro - 2 * pared);   // vaciado: abierta por arriba
    translate([0, 0, -1]) cylinder(h = fondo + 2, d = drenaje);              // drenaje
  }
}

// Pieza 2: plato, también abierto hacia arriba, colocado AL LADO (no debajo)
module plato() {
  d = diametro + 2 * pared + 8;
  difference() {
    cylinder(h = alto_plato, d = d);
    translate([0, 0, 2]) cylinder(h = alto_plato, d = d - 2 * pared);
  }
}

maceta();
translate([diametro / 2 + (diametro + 2 * pared + 8) / 2 + separacion, 0, 0]) plato();
```
"""

PLAN = """Antes de escribir código, escribe SOLO un PLAN breve (máximo 10 líneas, sin código):
1. Para qué sirve el objeto y cómo se usa.
2. Sus partes, cada una con sus medidas en mm (paredes finas, huecos).
3. Qué cara apoya en la cama, si es recipiente (boca arriba) y si son varias piezas (lado a lado)."""

CODIGO = "Ahora escribe el modelo OpenSCAD completo siguiendo el plan. Responde SOLO con un bloque ```openscad ... ```."

PALABRAS_MACIZO = r"\b(maciz|solid|bloque|cubo|ladrillo|tope|calzo|cuña|peso|contrapeso)"



def llamar_ia(mensajes, timeout=900, max_tokens=4096):
    cuerpo = json.dumps({
        "model": "aetherion-3d", "messages": mensajes, "temperature": 0.3, "top_p": 0.9,
        "max_tokens": max_tokens, "chat_template_kwargs": {"enable_thinking": False},
    }).encode()
    req = urllib.request.Request(LLM + "/v1/chat/completions", data=cuerpo,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())["choices"][0]["message"]["content"]


def extraer_codigo(respuesta):
    if "RECHAZADO" in respuesta and "```" not in respuesta:
        raise PermissionError("La IA ha rechazado la petición")
    bloques = re.findall(r"```(?:openscad|scad)?\s*\n(.*?)```", respuesta, re.S)
    codigo = (max(bloques, key=len) if bloques else respuesta).strip()
    if not codigo:
        raise ValueError("La IA no ha devuelto código")
    return codigo


PELIGROSO = [r"\bimport\s*\(", r"\binclude\s*<", r"\buse\s*<", r"\bsurface\s*\(", r"\bminkowski\s*\("]


def validar_codigo(codigo):
    if len(codigo) > MAX_CODIGO:
        raise ValueError("código demasiado largo")
    for p in PELIGROSO:
        if re.search(p, codigo):
            raise ValueError(f"el código usa una función no permitida ({p.strip(chr(92) + 'bs*(<')})")
    # $fn desmesurados disparan el tiempo de cálculo
    for v in re.findall(r"\$fn\s*=\s*(\d+)", codigo):
        if int(v) > 128:
            codigo = re.sub(r"\$fn\s*=\s*" + v, "$fn = 96", codigo)
    return codigo


# ───────────────────────── OpenSCAD aislado ─────────────────────────
def openscad(carpeta, args, timeout):
    uid = f"{os.getuid()}:{os.getgid()}"
    cmd = ["docker", "run", "--rm", "--network", "none", "--read-only", "--tmpfs", "/tmp:size=256m",
           "--memory", "3g", "--cpus", "4", "--pids-limit", "128", "--user", uid,
           "-v", f"{carpeta}:/w", "-w", "/w", IMAGEN_SCAD] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 30)
    except subprocess.TimeoutExpired:
        return 124, "Tiempo agotado: el modelo es demasiado complejo"
    return r.returncode, (r.stdout + r.stderr)


def construir(carpeta):
    """modelo.scad → modelo.stl. Devuelve (ok, mensaje de error para la IA)."""
    stl = carpeta / "modelo.stl"
    stl.unlink(missing_ok=True)
    code, salida = openscad(carpeta, ["timeout", "240", "openscad", "--export-format", "binstl",
                                      "-o", "/w/modelo.stl", "/w/modelo.scad"], 240)
    errores = [l for l in salida.splitlines() if "ERROR" in l or "WARNING: Object may not be a valid 2-manifold" in l]
    if code == 124:
        return False, "Tiempo agotado: simplifica el modelo (menos detalle, menos $fn, sin operaciones costosas)."
    if code or not stl.is_file() or stl.stat().st_size < 200:
        if "Current top level object is empty" in salida:
            errores.append("El resultado está vacío (la geometría no genera ningún sólido).")
        return False, "\n".join(errores[-8:]) or salida[-800:]
    return True, ""


def vista_png(carpeta, nombre="vista.png", camara="60,0,210", tam="900,700"):
    """Imagen de la pieza ya construida (se importa el STL: rápido, sin recalcular)."""
    (carpeta / "vista.scad").write_text('color("#5fd4c4") import("modelo.stl");\n')
    (carpeta / nombre).unlink(missing_ok=True)
    openscad(carpeta, ["timeout", "90", "xvfb-run", "-a", "openscad", "-o", f"/w/{nombre}",
                       f"--imgsize={tam}", "--viewall", "--autocenter", "--colorscheme=Tomorrow Night",
                       "--projection=p", f"--camera=0,0,0,{camara},0", "/w/vista.scad"], 90)
    (carpeta / "vista.scad").unlink(missing_ok=True)
    return (carpeta / nombre).is_file()


REVISAR = """Eres el revisor de calidad de un diseñador de piezas para impresión 3D.
Petición del cliente: «{texto}»
Plan del diseñador:
{plan}
Medidas REALES medidas en la pieza construida (ancho X × fondo Y × alto Z, en mm), de izquierda a derecha:
{piezas}
Te paso vistas de la pieza (Z hacia arriba; la última es desde abajo).
Busca SOLO errores graves y evidentes:
- ¿La forma no corresponde con lo pedido?
- ¿Un recipiente está cerrado por arriba o boca abajo?
- ¿Se pidieron piezas separadas y están pegadas o una encima de otra?
- ¿Falta alguna parte importante que se pidió, o hay piezas de más?
- ¿Las medidas reales contradicen claramente el plan o la petición? (p. ej. un plato que debía ser bajo es alto)
Responde SOLO con JSON: {{"correcto": true}} o {{"correcto": false, "problemas": ["…", "…"]}}"""


def revisar_visual(carpeta, texto, plan="", info=None):
    """La IA mira su propia pieza. Devuelve lista de problemas ([] si está bien o si no hay visión)."""
    import base64
    imagenes = []
    for nombre, camara in (("rev1.png", "60,0,210"), ("rev2.png", "35,0,40"), ("rev3.png", "150,0,30")):
        if vista_png(carpeta, nombre, camara, "640,500"):
            imagenes.append(base64.b64encode((carpeta / nombre).read_bytes()).decode())
            (carpeta / nombre).unlink(missing_ok=True)
    if not imagenes:
        return []
    piezas = (info or {}).get("piezas") or []
    lista = "\n".join(f"  {i + 1}) {x} × {y} × {z}" for i, (x, y, z) in enumerate(piezas)) or "  (desconocidas)"
    contenido = [{"type": "text", "text": REVISAR.format(texto=texto, plan=plan or "(sin plan)", piezas=lista)}] + \
                [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + b}} for b in imagenes]
    try:
        r = llamar_ia([{"role": "user", "content": contenido}], max_tokens=300)
    except urllib.error.HTTPError as e:
        if e.code in (400, 500, 501):     # el servidor no tiene visión (falta mmproj): se omite
            return []
        raise
    m = re.search(r"\{.*\}", r, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        return []
    if d.get("correcto", True):
        return []
    return [str(x)[:200] for x in d.get("problemas", [])][:5] or ["La pieza no corresponde con lo pedido"]


# ───────────────────────── comprobaciones del STL ─────────────────────────
def analizar_stl(ruta):
    datos = ruta.read_bytes()
    n = struct.unpack_from("<I", datos, 80)[0]
    if len(datos) < 84 + 50 * n:
        raise ValueError("STL incompleto")
    minimo = [float("inf")] * 3
    maximo = [float("-inf")] * 3
    aristas = Counter()
    vol = 0.0
    padre = {}

    def raiz(a):
        while padre.setdefault(a, a) != a:
            padre[a] = padre[padre[a]]
            a = padre[a]
        return a
    tris = []
    for i in range(n):
        base = 84 + 50 * i + 12
        v = [struct.unpack_from("<3f", datos, base + 12 * k) for k in range(3)]
        for p in v:
            for e in range(3):
                minimo[e] = min(minimo[e], p[e]); maximo[e] = max(maximo[e], p[e])
        q = [tuple(round(c, 4) for c in p) for p in v]
        tris.append((q, v))
        ra = raiz(q[0])
        for o in (q[1], q[2]):
            rb = raiz(o)
            if rb != ra:
                padre[rb] = ra
        for a, b in ((q[0], q[1]), (q[1], q[2]), (q[2], q[0])):
            aristas[(a, b) if a < b else (b, a)] += 1
        (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = v
        vol += (x1 * (y2 * z3 - y3 * z2) - x2 * (y1 * z3 - y3 * z1) + x3 * (y1 * z2 - y2 * z1)) / 6.0
    cerrado = n > 0 and all(c == 2 for c in aristas.values())
    medidas = [round(maximo[e] - minimo[e], 1) for e in range(3)]
    cajas = {}
    for q, v in tris:
        r = raiz(q[0])
        c = cajas.setdefault(r, [[float("inf")] * 3, [float("-inf")] * 3])
        for pt in v:
            for e in range(3):
                c[0][e] = min(c[0][e], pt[e]); c[1][e] = max(c[1][e], pt[e])
    # piezas separadas (ignoramos restos diminutos), de izquierda a derecha
    piezas = sorted((c for c in cajas.values() if max(c[1][e] - c[0][e] for e in range(3)) > 2), key=lambda c: c[0][0])
    return {"triangulos": n, "medidas_mm": medidas, "z_min": round(minimo[2], 2),
            "volumen_cm3": round(abs(vol) / 1000, 1), "cerrado": cerrado,
            "piezas": [[round(c[1][e] - c[0][e], 1) for e in range(3)] for c in piezas][:8]}


def avisos_de(info):
    av = []
    x, y, z = info["medidas_mm"]
    if sorted([x, y])[1] > CAMA[0] or sorted([x, y])[0] > CAMA[1] or z > CAMA[2]:
        av.append(f"No cabe en la cama de 220×220×250 mm ({x}×{y}×{z} mm): habrá que escalarla o partirla.")
    if abs(info["z_min"]) > 0.05:
        av.append(f"La pieza no apoya en z=0 (z mínima {info['z_min']} mm): el laminador la bajará a la cama.")
    if not info["cerrado"]:
        av.append("La malla no es totalmente estanca: la mayoría de laminadores la reparan solos.")
    for prob in info.get("problemas", []):
        av.append("Revisión: " + prob + " — prueba «Retocar» indicándolo con medidas.")
    if info.get("llenado", 0) > 0.6:
        av.append(f"Pieza muy maciza ({round(info['llenado'] * 100)} % de su caja): gastará mucho filamento; "
                  "en el laminador usa relleno bajo (10–15 %).")
    if min(x, y, z) < 0.8:
        av.append("Alguna dimensión es más fina de 0,8 mm: puede no imprimirse.")
    return av


# ───────────────────────── proceso completo ─────────────────────────
def modelar(texto, carpeta, previo=None, progreso=print, original=None):
    carpeta = Path(carpeta).resolve()
    carpeta.mkdir(parents=True, exist_ok=True)
    texto = texto.strip()[:MAX_TEXTO]
    if not texto:
        raise ValueError("Describe el objeto que quieres")
    if peticion_prohibida(texto):
        raise PermissionError("Esa petición no está permitida (armas o piezas peligrosas).")

    t0 = time.time()
    objetivo = texto                     # lo que debe cumplir la pieza final (para la revisión)
    if previo:
        previo = Path(previo)
        if not original and (previo.parent / "descripcion.txt").is_file():
            original = (previo.parent / "descripcion.txt").read_text(encoding="utf-8").strip()
        original = (original or "").strip()[:2000]
        if original:
            objetivo = original + "\nCAMBIO PEDIDO AHORA: " + texto
        if (previo.parent / "plan.txt").is_file():   # el plan original sigue valiendo como referencia
            (carpeta / "plan.txt").write_text((previo.parent / "plan.txt").read_text(encoding="utf-8") +
                                              "\n(Cambio pedido después: " + texto + ")\n", encoding="utf-8")
        codigo_previo = previo.read_text(encoding="utf-8")[:MAX_CODIGO]
        mensajes = [{"role": "system", "content": SISTEMA},
                    {"role": "user", "content": ("Pieza que pidió el cliente: " + original + "\n\n" if original else "") +
                     "Este es el modelo actual de esa pieza:\n```openscad\n" + codigo_previo +
                     "\n```\nAplica SOLO este cambio y conserva todo lo demás (la misma pieza, sus partes y su forma): " +
                     texto + "\n" + CODIGO}]
    else:
        # 1) plan: obliga a la IA a pensar en la función y las partes antes de dibujar
        progreso("[ia] Planificando la pieza")
        mensajes = [{"role": "system", "content": SISTEMA},
                    {"role": "user", "content": "Pieza a diseñar: " + texto + "\n\n" + PLAN}]
        plan = llamar_ia(mensajes, max_tokens=700).strip()
        (carpeta / "plan.txt").write_text(plan + "\n", encoding="utf-8")
        mensajes += [{"role": "assistant", "content": plan}, {"role": "user", "content": CODIGO}]

    (carpeta / "descripcion.txt").write_text(objetivo + "\n", encoding="utf-8")
    plan_txt = (carpeta / "plan.txt").read_text(encoding="utf-8") if (carpeta / "plan.txt").is_file() else ""
    quiere_macizo = re.search(PALABRAS_MACIZO, normalizar(texto)) is not None
    info = respaldo = None
    ok = False
    for intento in range(REINTENTOS + 1):
        progreso(f"[ia] {'Corrigiendo' if intento else 'Diseñando'} (intento {intento + 1})")
        respuesta = llamar_ia(mensajes)
        codigo = validar_codigo(extraer_codigo(respuesta))
        (carpeta / "modelo.scad").write_text(codigo + "\n", encoding="utf-8")
        progreso("[scad] Construyendo la pieza")
        ok, error = construir(carpeta)
        if ok:
            info = analizar_stl(carpeta / "modelo.stl")
            x, y, z = info["medidas_mm"]
            caja = max(x * y * z / 1000, 0.001)
            llenado = info["volumen_cm3"] / caja
            info["llenado"] = round(llenado, 2)
            if intento >= REINTENTOS:
                # último intento: se entrega, pero se revisa igualmente para avisar de lo que quede mal
                if VISION:
                    progreso("[revision] Revisión final")
                    pendientes = revisar_visual(carpeta, objetivo, plan_txt, info)
                    if pendientes:
                        progreso("[revision] Quedan problemas: " + "; ".join(pendientes)[:160])
                        info["problemas"] = pendientes
                break
            # 2) detector de bloques macizos
            problema = None
            if llenado > 0.6 and caja > 30 and not quiere_macizo:
                progreso(f"[revision] Pieza demasiado maciza ({round(llenado * 100)} % de su caja): rediseñando")
                problema = (f"El resultado es casi un bloque macizo ({x}×{y}×{z} mm, llena el {round(llenado * 100)} % "
                            "de su caja). Eso no sirve: gasta mucho plástico y no cumple la función. Rediséñalo como "
                            "una pieza funcional y ligera, con paredes de 2–3 mm y las partes del plan.")
            # 3) revisión visual: la IA mira dos vistas de su pieza
            elif VISION:
                progreso("[revision] Revisando la pieza visualmente")
                fallos = revisar_visual(carpeta, objetivo, plan_txt, info)
                if fallos:
                    progreso("[revision] Problemas: " + "; ".join(fallos)[:160])
                    problema = ("Al revisar la pieza construida se ven estos problemas:\n- " + "\n- ".join(fallos) +
                                "\nCorrígelos manteniendo lo que está bien.")
            if problema:
                for ext in ("scad", "stl"):          # por si el rediseño falla, nos quedamos con esta
                    (carpeta / f"modelo.{ext}").replace(carpeta / f"respaldo.{ext}")
                respaldo = info
                mensajes += [{"role": "assistant", "content": respuesta},
                             {"role": "user", "content": problema + " " + CODIGO}]
                continue
            break
        progreso("[scad] Error: " + error.splitlines()[0][:120] if error else "[scad] Error")
        mensajes += [{"role": "assistant", "content": respuesta},
                     {"role": "user", "content": "OpenSCAD da este error. Corrige el código y devuelve el modelo "
                      "completo:\n" + error}]
    if not ok and respaldo:
        for ext in ("scad", "stl"):
            (carpeta / f"respaldo.{ext}").replace(carpeta / f"modelo.{ext}")
        info, ok = respaldo, True
    for ext in ("scad", "stl"):
        (carpeta / f"respaldo.{ext}").unlink(missing_ok=True)
    if not ok:
        raise RuntimeError("No se ha podido construir la pieza tras varios intentos. Prueba a describirla de otra forma.")

    progreso("[png] Generando la imagen")
    hay_png = vista_png(carpeta)
    resultado = {"ok": True, "intentos": intento + 1, "segundos": round(time.time() - t0),
                 "imagen": hay_png, **info, "avisos": avisos_de(info)}
    (carpeta / "resultado.json").write_text(json.dumps(resultado, ensure_ascii=False, indent=2))
    progreso("[ok] " + json.dumps(resultado, ensure_ascii=False))
    return resultado


def main():
    ap = argparse.ArgumentParser(description="Aetherion 3D: texto → STL")
    ap.add_argument("--texto", required=True)
    ap.add_argument("--salida", required=True)
    ap.add_argument("--previo", help="modelo.scad anterior para retocarlo")
    ap.add_argument("--original", help="descripción de la pieza original (al retocar)")
    a = ap.parse_args()
    try:
        modelar(a.texto, a.salida, a.previo, progreso=lambda m: print(m, flush=True), original=a.original)
    except PermissionError as e:
        print("[rechazado]", e, flush=True); sys.exit(3)
    except Exception as e:
        print("[error]", e, flush=True); sys.exit(1)


if __name__ == "__main__":
    main()
