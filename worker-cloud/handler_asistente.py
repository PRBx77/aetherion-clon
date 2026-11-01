"""Handler asistente Aether: pregunta → respuesta JSON {respuesta, acciones}."""
import os, json, logging, requests, re
log = logging.getLogger("handler-asistente")

LLM_URL   = os.environ.get("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
LLM_KEY   = os.environ.get("OPENROUTER_API_KEY", "")
LLM_MODEL = os.environ.get("ASISTENTE_MODEL", "meta-llama/llama-3.3-70b-instruct")

TIPOS = ["nota", "recordatorio", "dato", "calculo", "borrar_nota", "borrar_recordatorio"]

SISTEMA = """Eres Aether, asistente personal en un móvil. Respondes SIEMPRE con JSON estricto:
{"respuesta": "texto corto y claro para el usuario", "acciones": [...]}

Las acciones son opcionales. Tipos permitidos:
- {"tipo":"nota","texto":"contenido de la nota"}
- {"tipo":"recordatorio","texto":"qué recordar","fecha":"YYYY-MM-DDTHH:MM"}
- {"tipo":"dato","archivo":"nombre_corto","contenido":"texto a guardar"}
- {"tipo":"calculo","expresion":"2+3*4"}
- {"tipo":"borrar_nota","id":"id_nota"}
- {"tipo":"borrar_recordatorio","id":"id_recordatorio"}

Normas:
- Habla en el idioma del usuario (español por defecto).
- Respuesta breve y útil, nunca más de 4000 caracteres.
- Si el usuario pide crear una nota, un recordatorio o guardar un dato, añade la acción correspondiente.
- Si pide un cálculo, mete una acción tipo "calculo" con la expresión.
- Si no hay nada que hacer, deja "acciones": [].
- NUNCA inventes datos del usuario que no estén en memoria o historial.
- Devuelve SOLO el JSON, sin markdown, sin ```, sin prosa adicional."""

def _llamar(mensajes):
    r = requests.post(LLM_URL,
        headers={"Authorization": f"Bearer {LLM_KEY}", "Content-Type": "application/json"},
        json={"model": LLM_MODEL, "messages": mensajes, "temperature": 0.4, "max_tokens": 1200,
              "response_format": {"type": "json_object"}},
        timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]

def _parse_json(s):
    s = re.sub(r"^```\w*\s*", "", s.strip())
    s = re.sub(r"\s*```$", "", s)
    return json.loads(s)

def procesar_asistente(j, base, headers):
    aid = j["id"]
    texto = j.get("texto", "")
    ahora = j.get("ahora", "")
    dia = j.get("dia", "")
    memoria = j.get("memoria", "")
    historial = j.get("historial", [])
    try:
        contexto = f"Hora local: {ahora}. Día: {dia}."
        if memoria:
            contexto += f"\nMemoria del usuario:\n{memoria[:1500]}"
        msgs = [{"role": "system", "content": SISTEMA + "\n\n" + contexto}]
        for m in historial[-8:]:
            rol = "assistant" if m["rol"] == "aether" else "user"
            msgs.append({"role": rol, "content": m["texto"][:1200]})
        msgs.append({"role": "user", "content": texto})
        salida = _llamar(msgs)
        datos = _parse_json(salida)
        respuesta = str(datos.get("respuesta", "No he podido responder."))[:4000]
        acciones = datos.get("acciones", []) if isinstance(datos.get("acciones"), list) else []
        # Limpiar acciones (filtro de tipos permitidos)
        acc_ok = []
        for a in acciones[:8]:
            if isinstance(a, dict) and a.get("tipo") in TIPOS:
                acc_ok.append({k: v for k, v in a.items() if k in ("tipo","texto","fecha","archivo","contenido","expresion","id") and v})
        log.info(f"Asistente {aid}: respuesta={len(respuesta)} chars, {len(acc_ok)} acciones")
        r = requests.post(f"{base}/worker/asistente/{aid}/resultado", headers=headers,
                          json={"respuesta": respuesta, "acciones": acc_ok}, timeout=15)
        r.raise_for_status()
    except Exception as e:
        log.error(f"Asistente {aid}: {e}")
        try:
            requests.post(f"{base}/worker/asistente/{aid}/error", headers=headers,
                          json={"error": str(e)[:300]}, timeout=10)
        except Exception:
            pass
