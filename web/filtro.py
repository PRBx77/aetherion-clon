"""Filtro de peticiones prohibidas (armas y piezas peligrosas). Copia del de modelar3d.py del X99."""
import re
import unicodedata

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
