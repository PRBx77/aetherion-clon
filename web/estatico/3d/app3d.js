// Aetherion 3D — panel web: pedir piezas, verlas en 3D, retocarlas y descargarlas.
import * as THREE from "./three.module.min.js";
import { STLLoader } from "./STLLoader.js";
import { OrbitControls } from "./OrbitControls.js";

const $ = s => document.querySelector(s);
const CSRF = $("#app3d").dataset.csrf;
const ESTADOS = { cola: "En cola", procesando: "Diseñando", hecho: "Lista", error: "Error" };
let seleccion = null;      // id de la pieza que se ve en el visor
let piezasCache = [];

// ───────────── formulario ─────────────
const texto = $("#texto");
texto.addEventListener("input", () => $("#contador").textContent = `${texto.value.length} / ${texto.maxLength}`);
document.querySelectorAll("#ejemplos .chip").forEach(b => b.onclick = () => {
  texto.value = b.textContent; texto.dispatchEvent(new Event("input")); texto.focus();
});

async function enviar(datos, boton, errorEl) {
  datos.append("csrf", CSRF);
  boton.disabled = true; errorEl.hidden = true;
  try {
    const r = await fetch("/api/3d", { method: "POST", body: datos });
    const j = await r.json().catch(() => ({ error: "Error del servidor" }));
    if (!r.ok) throw new Error(j.error || "Error");
    seleccion = j.id;
    await cargar();
    return true;
  } catch (e) { errorEl.textContent = e.message; errorEl.hidden = false; return false; }
  finally { boton.disabled = false; }
}

$("#nueva").addEventListener("submit", async e => {
  e.preventDefault();
  if (await enviar(new FormData(e.target), $("#enviar"), $("#error"))) {
    texto.value = ""; texto.dispatchEvent(new Event("input"));
  }
});

$("#retocar").addEventListener("submit", async e => {
  e.preventDefault();
  const d = new FormData(); d.append("texto", $("#texto-retoque").value); d.append("previo", seleccion);
  if (await enviar(d, e.target.querySelector("button"), $("#error"))) $("#texto-retoque").value = "";
});

// ───────────── visor 3D ─────────────
let escena, camara, render, controles, malla, rejilla, iniciado = false;

function iniciarVisor() {
  if (iniciado) return;
  iniciado = true;
  const cont = $("#visor");
  escena = new THREE.Scene();
  escena.background = new THREE.Color(0x0b111c);
  THREE.Object3D.DEFAULT_UP.set(0, 0, 1);                 // Z hacia arriba, como en la impresora
  camara = new THREE.PerspectiveCamera(40, 1, 0.5, 5000);
  camara.up.set(0, 0, 1);
  render = new THREE.WebGLRenderer({ antialias: true });
  render.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  cont.appendChild(render.domElement);
  controles = new OrbitControls(camara, render.domElement);
  controles.enableDamping = true;
  controles.addEventListener("change", pintar);
  escena.add(new THREE.HemisphereLight(0xdfefff, 0x1a2233, 1.1));
  const sol = new THREE.DirectionalLight(0xffffff, 1.6); sol.position.set(150, 250, 300); escena.add(sol);
  const contra = new THREE.DirectionalLight(0x88aaff, 0.5); contra.position.set(-200, -150, 100); escena.add(contra);
  // cama de impresión 220 × 220 mm
  rejilla = new THREE.GridHelper(220, 22, 0x2c3d5a, 0x182335);
  rejilla.rotation.x = Math.PI / 2;
  escena.add(rejilla);
  new ResizeObserver(ajustar).observe(cont);
  ajustar();
  (function bucle() { requestAnimationFrame(bucle); if (controles.update()) pintar(); })();
}

function ajustar() {
  const cont = $("#visor"), w = cont.clientWidth, h = cont.clientHeight;
  if (!w || !h) return;
  render.setSize(w, h); camara.aspect = w / h; camara.updateProjectionMatrix(); pintar();
}
function pintar() { if (render) render.render(escena, camara); }

function mostrarPieza(p) {
  seleccion = p.id;
  document.querySelectorAll(".pieza").forEach(li => li.classList.toggle("sel", li.dataset.id === p.id));
  const r = p.resultado || {};
  $("#visor-titulo").textContent = p.texto.length > 60 ? p.texto.slice(0, 60) + "…" : p.texto;
  const listo = p.estado === "hecho";
  $("#acciones").hidden = !listo; $("#retocar").hidden = !listo;
  const av = $("#avisos"); av.replaceChildren();
  if (!listo) {
    $("#visor-medidas").textContent = "";
    $("#visor-vacio").hidden = false;
    $("#visor-vacio").textContent = p.estado === "error" ? "No se pudo diseñar: " + (p.error || "") :
      "Esta pieza aún se está diseñando…";
    if (malla) { escena.remove(malla); malla = null; pintar(); }
    return;
  }
  const [x, y, z] = r.medidas_mm || [];
  let medidas = x ? `${x} × ${y} × ${z} mm` : "";
  if (r.volumen_cm3) medidas += ` · ~${Math.round(r.volumen_cm3 * 1.24)} g de PLA`;
  $("#visor-medidas").textContent = medidas;
  if ((r.piezas || []).length > 1) r.piezas.forEach((d, i) => av.append(el("li", { class: "info" }, `Pieza ${i + 1}: ${d[0]} × ${d[1]} × ${d[2]} mm`)));
  (r.avisos || []).forEach(a => av.append(el("li", {}, "⚠️ " + a)));
  $("#desc-stl").href = `/archivo3d/${p.id}/stl?descargar=1`;
  $("#desc-scad").href = `/archivo3d/${p.id}/scad?descargar=1`;
  cargarSTL(p.id);
}

function cargarSTL(id) {
  iniciarVisor();
  $("#visor-vacio").hidden = false; $("#visor-vacio").textContent = "Cargando la pieza…";
  new STLLoader().load(`/archivo3d/${id}/stl`, geo => {
    if (seleccion !== id) return;
    geo.computeBoundingBox();
    const b = geo.boundingBox;
    geo.translate(-(b.min.x + b.max.x) / 2, -(b.min.y + b.max.y) / 2, -b.min.z);   // centrada y apoyada en la cama
    geo.computeVertexNormals();
    if (malla) { escena.remove(malla); malla.geometry.dispose(); }
    malla = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0x5fd4c4, roughness: 0.55, metalness: 0.05 }));
    escena.add(malla);
    const t = new THREE.Vector3(); b.getSize(t);
    const d = Math.max(t.x, t.y, t.z, 30);
    controles.target.set(0, 0, t.z / 2);
    camara.position.set(d * 0.9, d * 1.5, d * 1.1);     // de frente (+Y) y desde arriba
    controles.update();
    $("#visor-vacio").hidden = true;
    ajustar();
  }, undefined, () => { $("#visor-vacio").textContent = "No se pudo cargar el archivo STL."; });
}

// ───────────── lista de piezas ─────────────
function el(tag, attrs = {}, ...hijos) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) k === "class" ? n.className = v : n.setAttribute(k, v);
  for (const h of hijos) n.append(h);
  return n;
}

function fila(p) {
  let est = ESTADOS[p.estado] || p.estado;
  if (p.estado === "cola" && p.posicion) est += ` · posición ${p.posicion}`;
  if (p.estado === "procesando") est += ` · ${p.fase || ""} ${p.progreso}%`;
  if (p.estado === "error") est += " (crédito devuelto)";
  const info = el("div", {},
    el("div", { class: "t" }, (p.previo_id ? "↻ " : "") + p.texto),
    el("div", { class: "estado " + p.estado }, est));
  if (p.estado === "cola" || p.estado === "procesando") {
    const i = el("i"); i.style.width = Math.max(3, p.progreso) + "%";
    info.append(el("div", { class: "barra" }, i));
  }
  const bot = el("div", { class: "botones" });
  if (p.estado === "hecho") {
    bot.append(el("a", { href: `/archivo3d/${p.id}/stl?descargar=1` }, "⬇ STL"));
  }
  if (p.estado === "hecho" || p.estado === "error") {
    const b = el("button", { class: "enlace", type: "button" }, "Borrar");
    b.onclick = async ev => {
      ev.stopPropagation();
      if (!confirm("¿Borrar esta pieza y sus archivos?")) return;
      await fetch(`/api/3d/${p.id}/borrar`, { method: "POST", headers: { "X-CSRF": CSRF } });
      if (seleccion === p.id) seleccion = null;
      cargar();
    };
    bot.append(b);
  }
  if (bot.childElementCount) info.append(bot);
  const mini = p.estado === "hecho" ? el("img", { src: `/archivo3d/${p.id}/png`, alt: "", loading: "lazy" }) : el("div", { class: "mini-vacia" }, "◇");
  const li = el("li", { class: "trabajo pieza" + (p.id === seleccion ? " sel" : ""), "data-id": p.id }, mini, info);
  li.onclick = () => mostrarPieza(p);
  return li;
}

let temporizador;
async function cargar() {
  clearTimeout(temporizador);
  try {
    const r = await fetch("/api/3d");
    if (r.redirected || r.status === 401) { location.href = "/entrar"; return; }
    const j = await r.json();
    $("#creditos").textContent = j.creditos;
    const antes = piezasCache.find(p => p.id === seleccion);
    piezasCache = j.piezas;
    const ul = $("#piezas"); ul.replaceChildren();
    if (!j.piezas.length) ul.append(el("li", { class: "nota" }, "Aún no has diseñado ninguna pieza."));
    j.piezas.forEach(p => ul.append(fila(p)));
    // el visor sigue a la pieza seleccionada (o a la más reciente) y se actualiza al terminar
    const actual = j.piezas.find(p => p.id === seleccion) || j.piezas[0];
    if (actual && (!antes || antes.id !== actual.id || antes.estado !== actual.estado)) mostrarPieza(actual);
    const activos = j.piezas.some(p => p.estado === "cola" || p.estado === "procesando");
    temporizador = setTimeout(cargar, activos ? 4000 : 30000);
  } catch { temporizador = setTimeout(cargar, 10000); }
}
cargar();
