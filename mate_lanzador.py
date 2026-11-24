"""Lanzador de Mate: lo arranca y, si se cae por un error, lo vuelve a levantar solo.
- Si lo cerrás vos ("Cerrar Mate"), no lo relanza (sale con código 0).
- Como mucho 3 reintentos cada 10 minutos (si se cae en loop, para y lo deja anotado).
El acceso directo de inicio y el del menú Inicio apuntan acá."""
import os, subprocess, sys, time
from pathlib import Path

AQUI = Path(__file__).resolve().parent
LOG = Path.home() / ".mate" / "mate.log"


def log(msg):
    try:
        LOG.parent.mkdir(exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f: f.write(time.strftime("%d/%m %H:%M:%S ") + "lanzador: " + msg + "\n")
    except Exception:
        pass


def main():
    caidas = []
    env = dict(os.environ, MATE_LAUNCHER_PID=str(os.getpid()))
    while True:
        p = subprocess.Popen([sys.executable, str(AQUI / "mate.py"), *sys.argv[1:]], cwd=str(AQUI), env=env)
        code = p.wait()
        if code == 0:
            return                                          # se cerró a propósito
        ahora = time.time()
        caidas = [t for t in caidas if ahora - t < 600] + [ahora]
        if len(caidas) > 3:
            log(f"Mate se cayó {len(caidas)} veces en 10 min (último código {code}): dejo de relanzarlo. Mirá crash.log")
            return
        log(f"Mate se cerró con error (código {code}): lo vuelvo a abrir")
        time.sleep(3)


if __name__ == "__main__":
    main()
