"use strict";
const $ = s => document.querySelector(s);
const CSRF = document.querySelector(".panel").dataset.csrf;
const ESTADOS = {cola: "En cola", procesando: "Generando", hecho: "Listo", error: "Error"};

// Idioma → filtra las voces de la casa (las clonadas hablan cualquier idioma)
function pintarVoces() {
  const idi = $("#idioma").value; let primera = null;
  for (const o of $("#voces-casa").children) {
    o.hidden = o.dataset.idioma !== idi;
    if (!o.hidden && !primera) primera = o;
  }
  if ($("#voz").selectedOptions[0]?.hidden) $("#voz").value = primera.value;
  $("#fila-expresividad").hidden = !$("#voz").value.startsWith("clon:");
}
$("#idioma").addEventListener("change", pintarVoces);
$("#voz").addEventListener("change", pintarVoces);
pintarVoces();

$("#guion").addEventListener("input", e =>
  $("#contador").textContent = `${e.target.value.length} / ${e.target.maxLength}`);

$("#foto").addEventListener("change", e => {
  const f = e.target.files[0]; if (!f) return;
  $("#vista").src = URL.createObjectURL(f); $("#vista").hidden = false; $("#foto-texto").hidden = true;
});

$("#nuevo").addEventListener("submit", async e => {
  e.preventDefault();
  const b = $("#enviar"); b.disabled = true; $("#error").hidden = true;
  const datos = new FormData(e.target); datos.append("csrf", CSRF);
  try {
    const r = await fetch("/api/trabajo", {method: "POST", body: datos});
    const j = await r.json().catch(() => ({error: "Error del servidor"}));
    if (!r.ok) throw new Error(j.error || "Error");
    $("#guion").value = ""; $("#contador").textContent = `0 / ${$("#guion").maxLength}`;
    cargar();
  } catch (err) { $("#error").textContent = err.message; $("#error").hidden = false; }
  finally { b.disabled = false; }
});

function el(tag, attrs = {}, ...hijos) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) k === "class" ? n.className = v : n.setAttribute(k, v);
  for (const h of hijos) n.append(h);
  return n;
}

function fila(t) {
  let est = ESTADOS[t.estado] || t.estado;
  if (t.estado === "cola" && t.posicion) est += ` · posición ${t.posicion}`;
  if (t.estado === "procesando") est += ` · ${t.fase || ""} ${t.progreso}%`;
  if (t.estado === "error") est += ` · ${t.error || ""} (créditos devueltos)`;
  const info = el("div", {}, el("div", {class: "t"}, t.guion), el("div", {class: "estado " + t.estado}, est));
  if (t.estado === "cola" || t.estado === "procesando") {
    const i = el("i"); i.style.width = Math.max(3, t.progreso) + "%";
    info.append(el("div", {class: "barra"}, i));
  }
  const bot = el("div", {class: "botones"});
  if (t.estado === "hecho") {
    const ver = el("button", {class: "enlace"}, "▶ Ver");
    ver.onclick = () => { $("#video").src = `/archivo/${t.id}/video`; $("#dialogo").showModal(); $("#video").play(); };
    bot.append(ver, el("a", {href: `/archivo/${t.id}/video?descargar=1`}, "⬇ Descargar"));
  }
  if (t.estado === "hecho" || t.estado === "error") {
    const borrar = el("button", {class: "enlace"}, "Borrar");
    borrar.onclick = async () => {
      if (!confirm("¿Borrar este trabajo y sus archivos?")) return;
      await fetch(`/api/trabajo/${t.id}/borrar`, {method: "POST", headers: {"X-CSRF": CSRF}});
      cargar();
    };
    bot.append(borrar);
  }
  if (bot.childElementCount) info.append(bot);
  return el("li", {class: "trabajo"}, el("img", {src: `/archivo/${t.id}/foto`, alt: "", loading: "lazy"}), info);
}

let temporizador;
async function cargar() {
  clearTimeout(temporizador);
  try {
    const r = await fetch("/api/trabajos");
    if (r.status === 401 || r.redirected) { location.href = "/entrar"; return; }
    const j = await r.json();
    $("#creditos").textContent = j.creditos;
    const ul = $("#trabajos"); ul.replaceChildren();
    if (!j.trabajos.length) ul.append(el("li", {class: "nota"}, "Aún no has generado ningún vídeo."));
    j.trabajos.forEach(t => ul.append(fila(t)));
    const activos = j.trabajos.some(t => t.estado === "cola" || t.estado === "procesando");
    temporizador = setTimeout(cargar, activos ? 4000 : 30000);
  } catch { temporizador = setTimeout(cargar, 10000); }
}
$("#dialogo").addEventListener("close", () => $("#video").pause());
cargar();

// ───────────── Mis voces ─────────────
const ESTADO_VOZ = {pendiente: "Pendiente de revisión", lista: "Lista para usar", rechazada: "Rechazada"};

async function cargarVoces(seleccionar) {
  let j;
  try { j = await (await fetch("/api/voces")).json(); } catch { return; }
  const ul = $("#voces"); ul.replaceChildren();
  if (!j.voces.length) ul.append(el("li", {class: "nota"}, "Aún no has clonado ninguna voz."));
  const grupo = $("#voces-clon"), elegida = $("#voz").value;
  grupo.replaceChildren();
  for (const v of j.voces) {
    let est = ESTADO_VOZ[v.estado] || v.estado;
    if (v.estado === "rechazada" && v.nota) est += ` · ${v.nota}`;
    const info = el("div", {}, el("div", {class: "t"}, v.nombre), el("div", {class: "estado " + v.estado}, est));
    const bot = el("div", {class: "botones"});
    const oir = el("button", {class: "enlace", type: "button"}, "▶ Muestra");
    oir.onclick = () => new Audio(`/api/voces/${v.id}/muestra`).play();
    const borrar = el("button", {class: "enlace", type: "button"}, "Borrar");
    borrar.onclick = async () => {
      if (!confirm(`¿Borrar la voz «${v.nombre}» y su grabación?`)) return;
      const r = await fetch(`/api/voces/${v.id}/borrar`, {method: "POST", headers: {"X-CSRF": CSRF}});
      if (!r.ok) alert((await r.json().catch(() => ({}))).error || "No se pudo borrar");
      cargarVoces();
    };
    bot.append(oir, borrar); info.append(bot);
    ul.append(el("li", {class: "trabajo voz"}, el("div", {class: "icono"}, "🎙"), info));
    if (v.estado === "lista") grupo.append(el("option", {value: "clon:" + v.id}, "Mi voz: " + v.nombre));
  }
  grupo.hidden = !grupo.childElementCount;
  const quiero = seleccionar ? "clon:" + seleccionar : elegida;
  if ([...$("#voz").options].some(o => o.value === quiero)) $("#voz").value = quiero;
  pintarVoces();
  const activas = j.voces.filter(v => v.estado !== "rechazada").length;
  $("#nueva-voz").hidden = activas >= j.max;
}

$("#nueva-voz").addEventListener("toggle", async e => {
  if (!e.target.open) return;
  try { $("#frase").textContent = (await (await fetch("/api/voces/frase")).json()).frase; }
  catch { $("#frase").textContent = "No se pudo cargar la frase. Recarga la página."; }
});

// Grabación con el micrófono del navegador
let grabadora = null, trozos = [], grabacion = null, reloj = null;
$("#grabar").addEventListener("click", async () => {
  if (grabadora && grabadora.state === "recording") { grabadora.stop(); return; }
  try {
    const flujo = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: false, noiseSuppression: true}});
    const tipo = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm"].find(t => MediaRecorder.isTypeSupported(t)) || "";
    grabadora = new MediaRecorder(flujo, tipo ? {mimeType: tipo} : {});
    trozos = [];
    grabadora.ondataavailable = e => e.data.size && trozos.push(e.data);
    grabadora.onstop = () => {
      flujo.getTracks().forEach(t => t.stop()); clearInterval(reloj);
      grabacion = new Blob(trozos, {type: grabadora.mimeType || "audio/webm"});
      $("#escucha").src = URL.createObjectURL(grabacion); $("#escucha").hidden = false;
      $("#grabar").textContent = "● Volver a grabar"; $("#grabar").classList.remove("grabando");
      $("#archivo-voz").value = "";
    };
    grabadora.start(500);
    const t0 = Date.now();
    $("#grabar").textContent = "■ Parar"; $("#grabar").classList.add("grabando");
    reloj = setInterval(() => {
      const s = Math.round((Date.now() - t0) / 1000);
      $("#crono").textContent = `${s} s` + (s < 15 ? " (sigue un poco más)" : "");
      if (s >= 60) grabadora.stop();
    }, 500);
  } catch (err) {
    $("#error-voz").textContent = "No se pudo usar el micrófono: " + err.message; $("#error-voz").hidden = false;
  }
});
$("#archivo-voz").addEventListener("change", e => {
  if (!e.target.files[0]) return;
  grabacion = null; $("#escucha").src = URL.createObjectURL(e.target.files[0]); $("#escucha").hidden = false;
});

$("#form-voz").addEventListener("submit", async e => {
  e.preventDefault();
  const b = $("#enviar-voz"); $("#error-voz").hidden = true;
  const archivo = $("#archivo-voz").files[0];
  if (!grabacion && !archivo) { $("#error-voz").textContent = "Graba tu voz o elige un archivo."; $("#error-voz").hidden = false; return; }
  const datos = new FormData(e.target); datos.append("csrf", CSRF);
  datos.append("muestra", grabacion || archivo, grabacion ? "grabacion.webm" : archivo.name);
  b.disabled = true;
  try {
    const r = await fetch("/api/voces", {method: "POST", body: datos});
    const j = await r.json().catch(() => ({error: "Error del servidor"}));
    if (!r.ok) throw new Error(j.error || "Error");
    e.target.reset(); grabacion = null; $("#escucha").hidden = true; $("#crono").textContent = "0 s";
    $("#grabar").textContent = "● Grabar"; $("#nueva-voz").open = false;
    await cargarVoces(j.estado === "lista" ? j.id : null);
    if (j.estado === "lista") $("#voz").scrollIntoView({behavior: "smooth", block: "center"});
  } catch (err) { $("#error-voz").textContent = err.message; $("#error-voz").hidden = false; }
  finally { b.disabled = false; }
});
cargarVoces();
