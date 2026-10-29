/* Aether — asistente personal de Aetherion.
 * Local-first: todo (chat, notas, recordatorios, archivos de datos) se guarda en ESTE móvil (IndexedDB).
 * El servidor solo recibe la pregunta con el contexto necesario, la responde y la olvida. */
"use strict";
const $ = (s, el = document) => el.querySelector(s);
const CSRF = document.body.dataset.csrf;
const PALABRA = /(?:^|\s)(?:a?e[tdz]h?er|ether|eter|ezer|eder|ather|aeter|heather|eather)\s+escuch\w*\b/;

// ───────────────────────── almacén local ─────────────────────────
const BD = {
  db: null,
  abrir() {
    return new Promise((ok, mal) => {
      const r = indexedDB.open("aether", 1);
      r.onupgradeneeded = () => {
        for (const n of ["mensajes", "notas", "recordatorios", "datos", "ajustes"])
          if (!r.result.objectStoreNames.contains(n)) r.result.createObjectStore(n, { keyPath: "id" });
      };
      r.onsuccess = () => { this.db = r.result; ok(); };
      r.onerror = () => mal(r.error);
    });
  },
  _req(store, modo, fn) {
    return new Promise((ok, mal) => {
      const tx = this.db.transaction(store, modo);
      const r = fn(tx.objectStore(store));
      tx.oncomplete = () => ok(r && r.result);
      tx.onerror = () => mal(tx.error);
    });
  },
  todos(s) { return this._req(s, "readonly", st => st.getAll()); },
  uno(s, id) { return this._req(s, "readonly", st => st.get(id)); },
  poner(s, obj) { return this._req(s, "readwrite", st => st.put(obj)); },
  borrar(s, id) { return this._req(s, "readwrite", st => st.delete(id)); },
  vaciar(s) { return this._req(s, "readwrite", st => st.clear()); },
};
const nuevoId = p => p + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

const AJ = { voz: true, vozNombre: "", manos: false };
async function cargarAjustes() {
  const a = await BD.uno("ajustes", "ajustes");
  if (a) Object.assign(AJ, a.valor);
}
const guardarAjustes = () => BD.poner("ajustes", { id: "ajustes", valor: { ...AJ } });

// ───────────────────────── utilidades ─────────────────────────
const pad = n => String(n).padStart(2, "0");
const fechaLocal = (d = new Date()) =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
const DIAS = ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"];
const deLocal = s => { const [f, h] = s.split("T"); const [a, m, d] = f.split("-").map(Number); const [hh, mm] = h.split(":").map(Number); return new Date(a, m - 1, d, hh, mm); };
const bonita = s => deLocal(s).toLocaleString("es-ES", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const normal = t => t.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[^\w\s]/g, " ").replace(/\s+/g, " ").trim();
function el(tag, attrs = {}, ...hijos) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
  }
  for (const h of hijos) if (h != null) e.append(h);
  return e;
}
function tostada(t, alerta = false, ms = 3500) {
  const x = $("#tostada"); x.textContent = t; x.className = "tostada" + (alerta ? " alerta" : ""); x.hidden = false;
  clearTimeout(tostada.t); tostada.t = setTimeout(() => (x.hidden = true), ms);
}
function descargar(nombre, contenido, tipo) {
  const url = URL.createObjectURL(new Blob([contenido], { type: tipo }));
  const a = el("a", { href: url, download: nombre }); document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// ───────────────────────── calculadora segura ─────────────────────────
// Las cuentas las hace el móvil, no la IA (los modelos pequeños se equivocan con los números).
function calcular(expr) {
  const s = expr.replace(/,/g, ".").replace(/×|x(?=\s*[\d(])/g, "*").replace(/÷/g, "/").replace(/\s+/g, "");
  let i = 0;
  const FUN = { sqrt: Math.sqrt, raiz: Math.sqrt, abs: Math.abs, round: Math.round, redondear: Math.round, ln: Math.log, log: Math.log10,
    sin: x => Math.sin(x * Math.PI / 180), cos: x => Math.cos(x * Math.PI / 180), tan: x => Math.tan(x * Math.PI / 180) };
  const ver = () => s[i];
  function num() {
    const m = /^(\d+\.?\d*|\.\d+)(e[+-]?\d+)?/i.exec(s.slice(i));
    if (m) { i += m[0].length; return parseFloat(m[0]); }
    const f = /^[a-z]+/i.exec(s.slice(i));
    if (f) {
      const n = f[0].toLowerCase(); i += n.length;
      if (n === "pi") return Math.PI;
      if (!FUN[n] || ver() !== "(") throw new Error("función desconocida: " + n);
      i++; const v = suma(); if (ver() !== ")") throw new Error("falta )"); i++; return FUN[n](v);
    }
    if (ver() === "(") { i++; const v = suma(); if (ver() !== ")") throw new Error("falta )"); i++; return v; }
    if (ver() === "-") { i++; return -num(); }
    if (ver() === "+") { i++; return num(); }
    throw new Error("expresión no válida");
  }
  function pot() { let b = num(); if (ver() === "%") { i++; b /= 100; } if (ver() === "^") { i++; return Math.pow(b, pot()); } return b; }
  function prod() { let v = pot(); while (ver() === "*" || ver() === "/") { const o = s[i++]; const d = pot(); v = o === "*" ? v * d : v / d; } return v; }
  function suma() { let v = prod(); while (ver() === "+" || ver() === "-") { const o = s[i++]; const d = prod(); v = o === "+" ? v + d : v - d; } return v; }
  const r = suma();
  if (i !== s.length || !isFinite(r)) throw new Error("expresión no válida");
  return r;
}
const formatoNum = n => n.toLocaleString("es-ES", { maximumFractionDigits: 6 });

// ───────────────────────── memoria que se envía al motor ─────────────────────────
async function memoriaParaMotor() {
  const LIM = 15000;
  const ahora = fechaLocal();
  const notas = (await BD.todos("notas")).sort((a, b) => b.creado - a.creado).slice(0, 60);
  const recs = (await BD.todos("recordatorios")).filter(r => r.fecha >= ahora.slice(0, 10)).sort((a, b) => a.fecha.localeCompare(b.fecha)).slice(0, 40);
  const datos = (await BD.todos("datos")).sort((a, b) => b.actualizado - a.actualizado);
  let m = "";
  if (recs.length) m += "RECORDATORIOS PENDIENTES:\n" + recs.map(r => `[${r.id}] ${r.fecha} ${r.texto}`).join("\n") + "\n\n";
  if (notas.length) m += "NOTAS:\n" + notas.map(n => `[${n.id}] (${fechaLocal(new Date(n.creado)).slice(0, 10)}) ${n.texto}`).join("\n") + "\n\n";
  for (const d of datos) {
    if (m.length > LIM) break;
    m += `ARCHIVO «${d.nombre}»:\n` + d.lineas.slice(-60).map(l => "- " + l.texto).join("\n") + "\n\n";
  }
  return m.slice(0, LIM);
}

// ───────────────────────── chat ─────────────────────────
let ocupado = false;
const caja = $("#mensajes");

function pintarMensaje(m) {
  const d = el("div", { class: "msg " + m.rol + (m.error ? " error" : "") });
  d.append(m.texto);
  if (m.chips && m.chips.length) d.append(el("div", { class: "chips" }, ...m.chips.map(chipDe)));
  d.append(el("span", { class: "hora" }, new Date(m.creado).toLocaleTimeString("es-ES", { hour: "2-digit", minute: "2-digit" })));
  return d;
}
function chipDe(c) {
  const e = el("div", { class: "chip" }, c.texto);
  if (c.ics) e.append(el("button", { class: "mini", type: "button", onclick: () => icsDe(c.ics) }, "📅 Al calendario"));
  return e;
}
async function pintarChat() {
  const ms = (await BD.todos("mensajes")).sort((a, b) => a.creado - b.creado).slice(-80);
  caja.replaceChildren();
  if (!ms.length) {
    caja.append(el("div", { class: "vacio" },
      el("p", {}, el("b", {}, "Hola, soy Aether.")),
      el("p", {}, "Prueba: «toma nota: mañana a las 5 revisar luces», «recuérdame el lunes a las 4 cita con el médico», «calcula 2,5 × 340 + 12 %» o «guarda en medidas motor: cilindro 1 a 12,4 bar»."),
      el("p", {}, "Lo que guardes se queda solo en este móvil.")));
  }
  for (const m of ms) caja.append(pintarMensaje(m));
  caja.scrollTop = caja.scrollHeight;
}
async function guardarMensaje(m) {
  await BD.poner("mensajes", m);
  const todos = (await BD.todos("mensajes")).sort((a, b) => a.creado - b.creado);
  for (const v of todos.slice(0, Math.max(0, todos.length - 300))) await BD.borrar("mensajes", v.id);
}

async function preguntar(texto) {
  texto = texto.trim();
  if (!texto || ocupado) return;
  ocupado = true; $("#enviar").disabled = true;
  if (caja.querySelector(".vacio")) caja.replaceChildren();
  const yo = { id: nuevoId("m"), rol: "yo", texto, creado: Date.now() };
  await guardarMensaje(yo);
  caja.append(pintarMensaje(yo));
  const espera = el("div", { class: "msg aether pensando" }, "Pensando");
  caja.append(espera); caja.scrollTop = caja.scrollHeight;
  try {
    const historial = (await BD.todos("mensajes")).filter(m => m.id !== yo.id && !m.error)
      .sort((a, b) => a.creado - b.creado).slice(-10).map(m => ({ rol: m.rol, texto: m.texto.slice(0, 1500) }));
    const ahora = new Date();
    const r = await fetch("/api/asistente", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF": CSRF },
      body: JSON.stringify({ texto, ahora: fechaLocal(ahora), dia: DIAS[ahora.getDay()], memoria: await memoriaParaMotor(), historial }),
    });
    if (r.status === 302 || r.redirected || r.status === 401) throw new Error("Tu sesión ha caducado: vuelve a entrar.");
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || "No he podido enviar el mensaje.");
    const res = await esperarRespuesta(d.id);
    const { texto: respuesta, chips } = await aplicarAcciones(res.respuesta || "", res.acciones || []);
    const msg = { id: nuevoId("m"), rol: "aether", texto: respuesta || "Hecho.", chips, creado: Date.now() };
    await guardarMensaje(msg);
    espera.replaceWith(pintarMensaje(msg));
    hablar(msg.texto);
    pintarListas();
  } catch (e) {
    const msg = { id: nuevoId("m"), rol: "aether", texto: e.message || "Error de conexión.", error: true, creado: Date.now() };
    await guardarMensaje(msg);
    espera.replaceWith(pintarMensaje(msg));
  } finally {
    ocupado = false; $("#enviar").disabled = false; caja.scrollTop = caja.scrollHeight;
  }
}
async function esperarRespuesta(id) {
  const fin = Date.now() + 150000;
  while (Date.now() < fin) {
    await new Promise(ok => setTimeout(ok, 700));
    let r;
    try { r = await fetch("/api/asistente/" + id, { credentials: "same-origin", cache: "no-store" }); }
    catch { continue; }                       // cobertura intermitente: se reintenta
    const d = await r.json().catch(() => ({}));
    if (d.estado === "hecho") return d;
    if (d.estado === "error" || d.estado === "perdido") throw new Error(d.error || "No he podido responder.");
  }
  throw new Error("Aether tarda demasiado. Vuelve a intentarlo.");
}

// Ejecuta en el móvil lo que pide la IA y devuelve el texto final y los avisos a mostrar.
async function aplicarAcciones(respuesta, acciones) {
  const chips = [];
  let resultados = [];
  for (const a of acciones) {
    if (!a.texto && a.contenido && (a.tipo === "nota" || a.tipo === "recordatorio")) a.texto = a.contenido;
    if (!a.contenido && a.texto && a.tipo === "dato") a.contenido = a.texto;
    try {
      if (a.tipo === "nota" && a.texto) {
        await BD.poner("notas", { id: nuevoId("n"), texto: a.texto, creado: Date.now() });
        chips.push({ texto: "📝 Nota guardada: " + a.texto });
      } else if (a.tipo === "recordatorio" && a.texto && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(a.fecha || "")) {
        const r = { id: nuevoId("r"), texto: a.texto, fecha: a.fecha, avisado: false, creado: Date.now() };
        await BD.poner("recordatorios", r);
        chips.push({ texto: `⏰ ${bonita(r.fecha)} · ${r.texto}`, ics: r });
        pedirPermisoAvisos(false);
      } else if (a.tipo === "dato" && a.archivo && a.contenido) {
        const nombre = a.archivo.trim().slice(0, 60);
        const todos = await BD.todos("datos");
        let f = todos.find(x => normal(x.nombre) === normal(nombre));
        if (!f) f = { id: nuevoId("d"), nombre, lineas: [], creado: Date.now() };
        f.lineas.push({ texto: a.contenido, creado: Date.now() });
        f.actualizado = Date.now();
        await BD.poner("datos", f);
        chips.push({ texto: `📁 Guardado en «${f.nombre}»: ${a.contenido}` });
      } else if (a.tipo === "calculo" && a.expresion) {
        const v = calcular(a.expresion);
        resultados.push(formatoNum(v));
        chips.push({ texto: `🧮 ${a.expresion} = ${formatoNum(v)}` });
      } else if (a.tipo === "borrar_nota" && a.id) {
        const n = await BD.uno("notas", a.id);
        if (n) { await BD.borrar("notas", a.id); chips.push({ texto: "🗑️ Nota borrada: " + n.texto }); }
      } else if (a.tipo === "borrar_recordatorio" && a.id) {
        const r = await BD.uno("recordatorios", a.id);
        if (r) { await BD.borrar("recordatorios", a.id); chips.push({ texto: "🗑️ Recordatorio borrado: " + r.texto }); }
      }
    } catch (e) {
      chips.push({ texto: "⚠️ No he podido hacer «" + (a.expresion || a.texto || a.tipo) + "»: " + e.message });
      resultados.push("?");
    }
  }
  let texto = respuesta;
  let k = 0;
  texto = texto.replace(/\{resultado\}/g, () => resultados[k++] ?? "?");
  if (resultados.length && k === 0) texto += (texto ? " " : "") + "Resultado: " + resultados.join(" · ") + ".";
  return { texto, chips };
}

// ───────────────────────── notas, recordatorios y datos ─────────────────────────
function icsDe(r) {
  const esc = t => t.replace(/[\\;,]/g, m => "\\" + m).replace(/\n/g, "\\n");
  const f = r.fecha.replace(/[-:]/g, "") + "00";
  const sello = new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+/, "");
  const ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Aetherion//Aether//ES", "BEGIN:VEVENT",
    `UID:${r.id}@aether.aetherion`, `DTSTAMP:${sello}`, `DTSTART:${f}`, "DURATION:PT30M", `SUMMARY:${esc(r.texto)}`,
    "BEGIN:VALARM", "TRIGGER:PT0M", "ACTION:DISPLAY", `DESCRIPTION:${esc(r.texto)}`, "END:VALARM",
    "END:VEVENT", "END:VCALENDAR"].join("\r\n");
  descargar("recordatorio.ics", ics, "text/calendar");
}
async function pintarListas() {
  const notas = (await BD.todos("notas")).sort((a, b) => b.creado - a.creado);
  $("#l-notas").replaceChildren(...(notas.length ? notas.map(n => el("li", {},
    el("div", { class: "txt" }, n.texto, el("small", {}, new Date(n.creado).toLocaleString("es-ES", { dateStyle: "medium", timeStyle: "short" }))),
    el("div", { class: "ops" }, el("button", { class: "mini x", onclick: async () => { await BD.borrar("notas", n.id); pintarListas(); } }, "✕"))))
    : [el("li", { class: "pasado" }, el("div", { class: "txt" }, "Aún no hay notas. Di «toma nota: …»"))]));

  const ahora = fechaLocal();
  const recs = (await BD.todos("recordatorios")).sort((a, b) => a.fecha.localeCompare(b.fecha));
  const pend = recs.filter(r => r.fecha >= ahora), pas = recs.filter(r => r.fecha < ahora).reverse();
  $("#l-avisos").replaceChildren(...[...pend, ...pas].map(r => el("li", { class: r.fecha < ahora ? "pasado" : "" },
    el("div", { class: "txt" }, r.texto, el("small", {}, bonita(r.fecha))),
    el("div", { class: "ops" },
      el("button", { class: "mini", onclick: () => icsDe(r), title: "Añadir al calendario del móvil" }, "📅"),
      el("button", { class: "mini x", onclick: async () => { await BD.borrar("recordatorios", r.id); pintarListas(); } }, "✕")))));
  if (!recs.length) $("#l-avisos").append(el("li", { class: "pasado" }, el("div", { class: "txt" }, "Sin recordatorios. Di «recuérdame el lunes a las 4 …»")));
  const g = $("#globo-avisos"); g.hidden = !pend.length; g.textContent = pend.length;

  const datos = (await BD.todos("datos")).sort((a, b) => b.actualizado - a.actualizado);
  $("#l-datos").replaceChildren(...datos.map(d => {
    const det = el("details", { class: "archivo-datos" },
      el("summary", {}, el("span", {}, "📁 " + d.nombre), el("small", {}, d.lineas.length + (d.lineas.length === 1 ? " dato" : " datos"))),
      el("ol", {}, ...d.lineas.map(l => el("li", {}, l.texto, el("small", {}, new Date(l.creado).toLocaleDateString("es-ES"))))),
      el("div", { class: "ops" },
        el("button", { class: "mini", onclick: () => descargar(d.nombre.replace(/[^\w\- ]+/g, "_") + ".txt",
          d.nombre + "\n\n" + d.lineas.map(l => `${new Date(l.creado).toLocaleString("es-ES")}\t${l.texto}`).join("\n"), "text/plain") }, "⬇ Descargar"),
        el("button", { class: "mini x", onclick: async () => { if (confirm(`¿Borrar el archivo «${d.nombre}»?`)) { await BD.borrar("datos", d.id); pintarListas(); } } }, "Borrar")));
    return det;
  }));
  if (!datos.length) $("#l-datos").append(el("p", { class: "nota" }, "Todavía no hay archivos."));
  const n = (await BD.todos("mensajes")).length;
  $("#aj-uso").textContent = `En este móvil: ${n} mensajes, ${notas.length} notas, ${recs.length} recordatorios, ${datos.length} archivos de datos.`;
}

$("#f-nota").addEventListener("submit", async e => {
  e.preventDefault(); const t = $("#nota-txt").value.trim(); if (!t) return;
  await BD.poner("notas", { id: nuevoId("n"), texto: t, creado: Date.now() }); $("#nota-txt").value = ""; pintarListas();
});
$("#f-aviso").addEventListener("submit", async e => {
  e.preventDefault(); const t = $("#aviso-txt").value.trim(), f = $("#aviso-fecha").value.slice(0, 16); if (!t || !f) return;
  await BD.poner("recordatorios", { id: nuevoId("r"), texto: t, fecha: f, avisado: false, creado: Date.now() });
  $("#aviso-txt").value = ""; pintarListas(); pedirPermisoAvisos(true);
});

// ───────────────────────── avisos ─────────────────────────
let registroSW = null;
function explicarAvisos() {
  const p = "Notification" in window ? Notification.permission : "no";
  $("#nota-avisos").textContent = p === "granted"
    ? "Los avisos saltan mientras Aether esté abierta o en segundo plano reciente. Para que suenen siempre, aunque cierres la app, pulsa 📅 y añádelo al calendario del móvil."
    : "Pulsa 📅 en cada recordatorio para añadirlo al calendario del móvil: así suena aunque Aether esté cerrada.";
}
async function pedirPermisoAvisos(insistir) {
  if (!("Notification" in window) || Notification.permission === "granted") return explicarAvisos();
  if (Notification.permission === "default" && insistir) await Notification.requestPermission().catch(() => {});
  explicarAvisos();
}
async function revisarAvisos() {
  const ahora = fechaLocal();
  for (const r of await BD.todos("recordatorios")) {
    if (r.avisado || r.fecha > ahora) continue;
    r.avisado = true; await BD.poner("recordatorios", r);
    const tarde = (deLocal(ahora) - deLocal(r.fecha)) / 60000 > 30;
    tostada(`⏰ ${tarde ? "Pasado: " : ""}${r.texto}`, true, 10000);
    if (!tarde) hablar("Recordatorio: " + r.texto);
    if ("Notification" in window && Notification.permission === "granted") {
      const op = { body: r.texto, tag: r.id, icon: "/static/asistente/icono-192.png", badge: "/static/asistente/icono-192.png" };
      try { registroSW ? await registroSW.showNotification("⏰ Aether", op) : new Notification("⏰ Aether", op); } catch {}
    }
    pintarListas();
  }
}

// ───────────────────────── voz ─────────────────────────
const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
let reco = null, modoManos = false, esperandoOrden = false, hablando = false, pulsado = false, tOrden = null;

function vozElegida() {
  const vs = speechSynthesis.getVoices().filter(v => v.lang.toLowerCase().startsWith("es"));
  return vs.find(v => v.name === AJ.vozNombre) || vs.find(v => v.lang === "es-ES" && v.localService) || vs.find(v => v.lang === "es-ES") || vs[0];
}
function rellenarVoces() {
  if (!("speechSynthesis" in window)) return;
  const sel = $("#aj-voz-nombre");
  const vs = speechSynthesis.getVoices().filter(v => v.lang.toLowerCase().startsWith("es"));
  sel.closest("label").hidden = !vs.length;
  sel.replaceChildren(...vs.map(v => { const o = el("option", { value: v.name }, `${v.name} (${v.lang})`); if (v.name === (vozElegida() || {}).name) o.selected = true; return o; }));
}
function hablar(t) {
  if (!AJ.voz || !("speechSynthesis" in window) || !t) return;
  const limpio = t.replace(/[*_#`>]/g, "").replace(/\p{Extended_Pictographic}/gu, "").slice(0, 1200);
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(limpio);
  const v = vozElegida(); if (v) { u.voice = v; u.lang = v.lang; } else u.lang = "es-ES";
  u.rate = 1.03;
  hablando = true; pararReco();
  u.onend = u.onerror = () => { hablando = false; if (modoManos) setTimeout(arrancarReco, 300); };
  speechSynthesis.speak(u);
}
function pitido(f = 880) {
  try {
    const ctx = pitido.ctx || (pitido.ctx = new (window.AudioContext || window.webkitAudioContext)());
    const o = ctx.createOscillator(), g = ctx.createGain(); o.frequency.value = f; o.connect(g); g.connect(ctx.destination);
    g.gain.setValueAtTime(0.15, ctx.currentTime); g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.18);
    o.start(); o.stop(ctx.currentTime + 0.2);
  } catch {}
}
function estadoOido() {
  const b = $("#oido");
  b.setAttribute("aria-pressed", modoManos ? "true" : "false");
  b.classList.toggle("orden", esperandoOrden);
  $("#oido-txt").textContent = !modoManos ? "Manos libres" : esperandoOrden ? "Dime…" : "«Aether escucha»";
  $("#escuchando").hidden = !esperandoOrden && !pulsado;
}
function crearReco(continuo) {
  const r = new SR();
  r.lang = "es-ES"; r.continuous = continuo; r.interimResults = !continuo; r.maxAlternatives = 1;
  return r;
}
function pararReco() { if (reco) { const r = reco; reco = null; r.onend = null; try { r.abort(); } catch {} } }
function arrancarReco() {
  if (!SR || !modoManos || hablando || reco || document.hidden) return;
  reco = crearReco(true);
  reco.onresult = ev => {
    for (let k = ev.resultIndex; k < ev.results.length; k++) {
      if (!ev.results[k].isFinal) continue;
      const dicho = ev.results[k][0].transcript;
      const n = normal(dicho);
      if (esperandoOrden) { finOrden(); if (n) preguntar(dicho); continue; }
      const m = PALABRA.exec(n);
      if (!m) continue;
      const resto = n.slice(m.index + m[0].length).trim();
      // recupera el texto original (con tildes y mayúsculas) a partir de la palabra «escucha…»
      const orig = dicho.replace(/^.*?escuch\S*[\s,.:]*/i, "").trim();
      if (resto.length > 2) { pitido(); preguntar(orig || resto); }
      else { esperandoOrden = true; pitido(); estadoOido(); tOrden = setTimeout(finOrden, 9000); }
    }
  };
  reco.onerror = ev => {
    if (ev.error === "not-allowed" || ev.error === "service-not-allowed") {
      modoManos = false; estadoOido(); tostada("Sin permiso para usar el micrófono.", true);
    }
  };
  reco.onend = () => { reco = null; if (modoManos && !hablando) setTimeout(arrancarReco, 250); };
  try { reco.start(); } catch { reco = null; }
}
function finOrden() { esperandoOrden = false; clearTimeout(tOrden); estadoOido(); }
function cambiarManos(activar) {
  modoManos = activar; esperandoOrden = false;
  if (activar) { arrancarReco(); tostada("Manos libres: di «Aether escucha» y tu orden."); } else pararReco();
  estadoOido();
}
function hablarUnaVez() {
  if (pulsado) { pararReco(); return; }
  const estabaManos = modoManos; if (estabaManos) { modoManos = false; pararReco(); }
  speechSynthesis && speechSynthesis.cancel();
  const r = crearReco(false); reco = r; pulsado = true; $("#mic").classList.add("activo"); estadoOido();
  let ultimo = "";
  r.onresult = ev => {
    ultimo = Array.from(ev.results).map(x => x[0].transcript).join(" ");
    $("#texto").value = ultimo;
    if (ev.results[ev.results.length - 1].isFinal) { try { r.stop(); } catch {} }
  };
  r.onerror = ev => { if (ev.error === "not-allowed") tostada("Sin permiso para usar el micrófono.", true); };
  r.onend = () => {
    reco = null; pulsado = false; $("#mic").classList.remove("activo"); estadoOido();
    if (ultimo.trim()) { $("#texto").value = ""; ajustarAltura(); preguntar(ultimo); }
    if (estabaManos) { modoManos = true; setTimeout(arrancarReco, 400); estadoOido(); }
  };
  try { r.start(); } catch { reco = null; pulsado = false; $("#mic").classList.remove("activo"); }
}
if (SR) {
  $("#mic").hidden = false; $("#oido").hidden = false;
  $("#mic").addEventListener("click", hablarUnaVez);
  $("#oido").addEventListener("click", () => cambiarManos(!modoManos));
  document.addEventListener("visibilitychange", () => { if (document.hidden) pararReco(); else if (modoManos) arrancarReco(); });
}

// ───────────────────────── copia de seguridad ─────────────────────────
const b64 = buf => btoa(String.fromCharCode(...new Uint8Array(buf)));
const deB64 = s => Uint8Array.from(atob(s), c => c.charCodeAt(0));
async function claveDe(pass, sal) {
  const base = await crypto.subtle.importKey("raw", new TextEncoder().encode(pass), "PBKDF2", false, ["deriveKey"]);
  return crypto.subtle.deriveKey({ name: "PBKDF2", salt: sal, iterations: 250000, hash: "SHA-256" }, base,
    { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
}
$("#aj-exportar").addEventListener("click", async () => {
  const todo = { app: "aether", version: 1, fecha: new Date().toISOString() };
  for (const s of ["mensajes", "notas", "recordatorios", "datos", "ajustes"]) todo[s] = await BD.todos(s);
  const pass = $("#aj-clave").value;
  let salida;
  if (pass) {
    if (!crypto.subtle) return tostada("Este navegador no puede cifrar.", true);
    const sal = crypto.getRandomValues(new Uint8Array(16)), iv = crypto.getRandomValues(new Uint8Array(12));
    const cif = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, await claveDe(pass, sal), new TextEncoder().encode(JSON.stringify(todo)));
    salida = JSON.stringify({ app: "aether", cifrado: true, sal: b64(sal), iv: b64(iv), datos: b64(cif) });
  } else {
    if (!confirm("Sin contraseña la copia queda legible para quien tenga el archivo. ¿Continuar?")) return;
    salida = JSON.stringify(todo);
  }
  descargar(`aether-copia-${fechaLocal().slice(0, 10)}.aether`, salida, "application/json");
  tostada("Copia guardada. Guárdala en un sitio seguro (Drive, iCloud, ordenador…).");
});
$("#aj-importar").addEventListener("change", async e => {
  const f = e.target.files[0]; e.target.value = ""; if (!f) return;
  try {
    let d = JSON.parse(await f.text());
    if (d.app !== "aether") throw new Error("No es una copia de Aether.");
    if (d.cifrado) {
      const pass = $("#aj-clave").value || prompt("Contraseña de la copia:");
      if (!pass) return;
      try {
        const plano = await crypto.subtle.decrypt({ name: "AES-GCM", iv: deB64(d.iv) }, await claveDe(pass, deB64(d.sal)), deB64(d.datos));
        d = JSON.parse(new TextDecoder().decode(plano));
      } catch { throw new Error("Contraseña incorrecta."); }
    }
    if (!confirm("Se sustituirán los datos de este móvil por los de la copia. ¿Seguir?")) return;
    for (const s of ["mensajes", "notas", "recordatorios", "datos", "ajustes"]) {
      await BD.vaciar(s); for (const x of d[s] || []) if (x && x.id) await BD.poner(s, x);
    }
    await cargarAjustes(); pintarAjustes(); await pintarChat(); await pintarListas();
    tostada("Copia recuperada.");
  } catch (err) { tostada(err.message || "No se ha podido leer la copia.", true); }
});
$("#aj-borrar").addEventListener("click", async () => {
  if (!confirm("¿Borrar TODO lo de Aether en este móvil (chat, notas, recordatorios, archivos)? No se puede deshacer.")) return;
  for (const s of ["mensajes", "notas", "recordatorios", "datos"]) await BD.vaciar(s);
  await pintarChat(); await pintarListas(); tostada("Datos borrados de este móvil.");
});

// ───────────────────────── ajustes e instalación ─────────────────────────
function pintarAjustes() {
  $("#aj-voz").checked = AJ.voz; $("#aj-manos").checked = AJ.manos; rellenarVoces(); explicarAvisos();
}
$("#aj-voz").addEventListener("change", e => { AJ.voz = e.target.checked; guardarAjustes(); if (!AJ.voz) speechSynthesis.cancel(); });
$("#aj-manos").addEventListener("change", e => { AJ.manos = e.target.checked; guardarAjustes(); });
$("#aj-voz-nombre").addEventListener("change", e => { AJ.vozNombre = e.target.value; guardarAjustes(); hablar("Hola, soy Aether."); });
$("#aj-avisos").addEventListener("click", () => pedirPermisoAvisos(true));
if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = rellenarVoces;
let promptInstalar = null;
window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); promptInstalar = e; $("#aj-instalar").hidden = false; });
$("#aj-instalar").addEventListener("click", async () => { if (promptInstalar) { promptInstalar.prompt(); promptInstalar = null; $("#aj-instalar").hidden = true; } });
if (/iphone|ipad|ipod/i.test(navigator.userAgent) && !navigator.standalone) $("#aj-instalar-ios").hidden = false;

// ───────────────────────── pestañas y entrada ─────────────────────────
$("#pestanas").addEventListener("click", e => {
  const b = e.target.closest("button[data-v]"); if (!b) return;
  for (const x of document.querySelectorAll("#pestanas button")) x.classList.toggle("activa", x === b);
  for (const v of document.querySelectorAll(".vista")) v.classList.toggle("activa", v.id === "v-" + b.dataset.v);
  if (b.dataset.v === "chat") caja.scrollTop = caja.scrollHeight;
});
const area = $("#texto");
function ajustarAltura() { area.style.height = "auto"; area.style.height = Math.min(140, area.scrollHeight) + "px"; }
area.addEventListener("input", ajustarAltura);
area.addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); $("#f-chat").requestSubmit(); } });
$("#f-chat").addEventListener("submit", e => {
  e.preventDefault();
  if (ocupado) return tostada("Espera a que Aether responda…");
  const t = area.value; area.value = ""; ajustarAltura(); preguntar(t);
});

// ───────────────────────── arranque ─────────────────────────
(async () => {
  try { await BD.abrir(); }
  catch { tostada("Este navegador no permite guardar datos (¿modo privado?).", true, 8000); return; }
  if (navigator.storage && navigator.storage.persist) navigator.storage.persist().catch(() => {});
  await cargarAjustes(); pintarAjustes();
  await pintarChat(); await pintarListas();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/asistente/sw.js", { scope: "/asistente" }).then(r => (registroSW = r)).catch(() => {});
  }
  revisarAvisos(); setInterval(revisarAvisos, 20000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) revisarAvisos(); });
  if (SR && AJ.manos) cambiarManos(true);
})();
