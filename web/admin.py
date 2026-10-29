#!/usr/bin/env python3
"""Administración de Aetherion Diseño (se ejecuta en el propio VPS).

  admin.py invitar --nivel diamante --creditos 20 [--dias 14] [--cantidad 5] [--nota "Juan"]
  admin.py invitaciones            lista los códigos
  admin.py revocar CODIGO
  admin.py usuarios                lista las cuentas
  admin.py creditos NOMBRE +10     suma (o -5 resta) créditos
  admin.py nivel NOMBRE esmeralda
  admin.py bloquear NOMBRE / desbloquear NOMBRE
  admin.py hacer-admin NOMBRE / quitar-admin NOMBRE   acceso al panel web /admin
  admin.py cola                    trabajos pendientes
  admin.py voces                   voces clonadas (pendientes primero) con la ruta del audio
  admin.py aprobar-voz ID / rechazar-voz ID [--motivo "..."]
"""
import argparse
import secrets
import sqlite3
import sys
import time

import app as web

LETRAS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin 0/O ni 1/I


def nuevo_codigo():
    p = lambda: "".join(secrets.choice(LETRAS) for _ in range(4))  # noqa: E731
    return f"AETH-{p()}-{p()}-{p()}"


def fecha(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "—"


def main():
    ap = argparse.ArgumentParser(description="Administración de Aetherion Diseño")
    s = ap.add_subparsers(dest="cmd", required=True)
    i = s.add_parser("invitar")
    i.add_argument("--nivel", choices=list(web.NIVELES), required=True)
    i.add_argument("--creditos", type=int, default=10)
    i.add_argument("--dias", type=int, default=14)
    i.add_argument("--cantidad", type=int, default=1)
    i.add_argument("--nota", default="")
    s.add_parser("invitaciones")
    s.add_parser("usuarios")
    s.add_parser("cola")
    r = s.add_parser("revocar"); r.add_argument("codigo")
    c = s.add_parser("creditos"); c.add_argument("nombre"); c.add_argument("cambio", type=int)
    n = s.add_parser("nivel"); n.add_argument("nombre"); n.add_argument("nivel", choices=list(web.NIVELES))
    s.add_parser("voces")
    av = s.add_parser("aprobar-voz"); av.add_argument("id")
    rv = s.add_parser("rechazar-voz"); rv.add_argument("id"); rv.add_argument("--motivo", default="La grabación no es válida")
    ha = s.add_parser("hacer-admin"); ha.add_argument("nombre")
    qa = s.add_parser("quitar-admin"); qa.add_argument("nombre")
    b = s.add_parser("bloquear"); b.add_argument("nombre")
    d = s.add_parser("desbloquear"); d.add_argument("nombre")
    a = ap.parse_args()

    db = sqlite3.connect(web.DB)
    db.row_factory = sqlite3.Row
    ahora = int(time.time())

    if a.cmd == "invitar":
        for _ in range(a.cantidad):
            cod = nuevo_codigo()
            db.execute("INSERT INTO invitaciones (codigo, nivel, creditos, caduca, nota, creado) VALUES (?,?,?,?,?,?)",
                       (cod, a.nivel, a.creditos, ahora + a.dias * 86400, a.nota, ahora))
            print(f"{cod}   {web.NIVELES[a.nivel]}  {a.creditos} créditos  caduca {fecha(ahora + a.dias * 86400)}")
        db.commit()
    elif a.cmd == "invitaciones":
        for r in db.execute("SELECT i.*, u.nombre FROM invitaciones i LEFT JOIN usuarios u ON u.id=i.usado_por "
                            "ORDER BY i.creado DESC"):
            est = ("REVOCADA" if r["revocada"] else f"usada por {r['nombre']}" if r["usado_por"]
                   else "CADUCADA" if r["caduca"] < ahora else "libre")
            print(f"{r['codigo']}  {r['nivel']:<9} {r['creditos']:>4} cr  caduca {fecha(r['caduca'])}  {est:<22} {r['nota'] or ''}")
    elif a.cmd == "revocar":
        n = db.execute("UPDATE invitaciones SET revocada=1 WHERE codigo=? AND usado_por IS NULL",
                       (a.codigo.upper(),)).rowcount
        db.commit()
        print("Revocada" if n else "No existe o ya se usó (para cortar el acceso: bloquear NOMBRE)")
    elif a.cmd == "usuarios":
        for r in db.execute("SELECT * FROM usuarios ORDER BY creado"):
            print(f"{r['id']:>4} {r['nombre']:<24} {r['nivel']:<9} {r['creditos']:>5} cr  {r['origen']:<10} "
                  f"{'activo' if r['activo'] else 'BLOQUEADO'}  alta {fecha(r['creado'])}")
    elif a.cmd == "creditos":
        n = db.execute("UPDATE usuarios SET creditos=MAX(0, creditos+?) WHERE nombre=?", (a.cambio, a.nombre)).rowcount
        db.commit()
        print("Hecho" if n else "No existe ese usuario")
    elif a.cmd == "nivel":
        n = db.execute("UPDATE usuarios SET nivel=? WHERE nombre=?", (a.nivel, a.nombre)).rowcount
        db.commit()
        print("Hecho" if n else "No existe ese usuario")
    elif a.cmd in ("hacer-admin", "quitar-admin"):
        n = db.execute("UPDATE usuarios SET admin=? WHERE nombre=?", (int(a.cmd == "hacer-admin"), a.nombre)).rowcount
        db.commit()
        print(("Hecho. Entra en /admin" if a.cmd == "hacer-admin" else "Hecho") if n else "No existe ese usuario")
    elif a.cmd in ("bloquear", "desbloquear"):
        n = db.execute("UPDATE usuarios SET activo=? WHERE nombre=?", (int(a.cmd == "desbloquear"), a.nombre)).rowcount
        db.commit()
        print("Hecho" if n else "No existe ese usuario")
    elif a.cmd == "voces":
        for r in db.execute("SELECT v.*, u.nombre AS usuario FROM voces v JOIN usuarios u ON u.id=v.usuario_id "
                            "ORDER BY v.estado!='pendiente', v.creado DESC"):
            print(f"{r['id']}  {r['estado']:<10} {r['usuario']:<16} «{r['nombre']}»  {fecha(r['creado'])}")
            print(f"      frase: {r['frase']}")
            print(f"      audio: {web.ARCHIVOS / str(r['usuario_id']) / 'voces' / r['archivo']}")
    elif a.cmd in ("aprobar-voz", "rechazar-voz"):
        nuevo = "lista" if a.cmd == "aprobar-voz" else "rechazada"
        nota = None if nuevo == "lista" else a.motivo
        n = db.execute("UPDATE voces SET estado=?, nota=? WHERE id=?", (nuevo, nota, a.id)).rowcount
        db.commit()
        print(f"Voz {nuevo}" if n else "No existe esa voz")
    elif a.cmd == "cola":
        for r in db.execute("SELECT t.*, u.nombre FROM trabajos t JOIN usuarios u ON u.id=t.usuario_id "
                            "WHERE estado IN ('cola','procesando') ORDER BY t.creado"):
            print(f"{r['id']}  {r['nombre']:<16} {r['estado']:<11} {r['progreso']:>3}%  {r['calidad']}  {fecha(r['creado'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
