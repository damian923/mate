"""Mate en macOS: todo lo que en Windows hace con la API de Windows, acá con AppleScript, `open` y Cocoa.

Permisos que macOS le pide al usuario la primera vez (Ajustes del Sistema > Privacidad y seguridad):
  - Micrófono (para escucharte)
  - Accesibilidad (para escribir y apretar teclas en Slack, WhatsApp y Granola)
  - Automatización (para controlar Spotify y System Events)
"""
import os, re, subprocess, sys, time, difflib, urllib.parse
from pathlib import Path

IS_MAC = sys.platform == "darwin"


def osa(script, timeout=15):
    """Corre AppleScript y devuelve lo que imprime."""
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


def _q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------- sistema ----------------
def open_uri(uri):
    subprocess.Popen(["open", uri])


def open_background(uri):
    """Abre algo sin pasarle el foco (ej. Spotify para que aparezca como dispositivo)."""
    subprocess.Popen(["open", "-g", uri])


def lock():
    # Bloquea la pantalla (equivale a Ctrl+Cmd+Q)
    osa('tell application "System Events" to keystroke "q" using {control down, command down}')


def volume(direction, steps=5):
    if direction == "mute":
        osa("set volume output muted not (output muted of (get volume settings))"); return
    d = 6 * steps // 5 if direction == "up" else -6 * steps // 5
    osa(f"set volume output volume ((output volume of (get volume settings)) + ({d}))")


def media(what):
    """play/pausa/siguiente/anterior sobre la app que esté sonando (Spotify o Música)."""
    cmd = {"playpause": "playpause", "next": "next track", "prev": "previous track"}[what]
    for app in ("Spotify", "Music"):
        if osa(f'application {_q(app)} is running') == "true":
            osa(f"tell application {_q(app)} to {cmd}"); return


def now_playing():
    """Lo que está sonando en Spotify (o Música): {'title','artist','playing','app'} o None."""
    for app in ("Spotify", "Music"):
        try:
            if osa(f'application {_q(app)} is running', 3) != "true": continue
            out = osa(f'tell application {_q(app)} to if player state is not stopped then '
                      f'return (name of current track) & "\\n" & (artist of current track) & "\\n" & (player state as text)', 3)
            if out:
                t, a, st = (out.split("\n") + ["", "", ""])[:3]
                return {"title": t, "artist": a, "playing": st == "playing", "app": app}
        except Exception:
            pass
    return None


def clipboard_get():
    return subprocess.run(["pbpaste"], capture_output=True, text=True).stdout


def clipboard_set_text(t):
    subprocess.run(["pbcopy"], input=t, text=True)


def clipboard_set_file(path):
    osa(f"set the clipboard to (POSIX file {_q(path)})")


def beep():
    osa("beep")


# ---------------- apps ----------------
APP_DIRS = [Path("/Applications"), Path("/System/Applications"), Path("/System/Applications/Utilities"),
            Path.home() / "Applications"]


def list_apps():
    out = {}
    for d in APP_DIRS:
        if d.exists():
            for p in d.glob("*.app"): out[p.stem.lower()] = p.stem
    return out


def open_app(name):
    apps = list_apps()
    hit = apps.get(name.lower()) or next((v for k, v in apps.items() if name.lower() in k), None)
    if not hit:
        m = difflib.get_close_matches(name.lower(), list(apps), 1, 0.6)
        hit = apps[m[0]] if m else None
    if not hit: return None
    subprocess.Popen(["open", "-a", hit]); return hit


def close_app(name):
    """Como Cmd+Q: la app puede pedirte guardar. Nunca cierra a Mate ni al Finder."""
    running = osa('tell application "System Events" to get name of (processes where background only is false)').split(", ")
    hits = [p for p in running if name.lower() in p.lower() and p not in ("Finder", "Python", "Mate")]
    for p in hits: osa(f"tell application {_q(p)} to quit")
    return hits


def is_running(name):
    return osa(f'application {_q(name)} is running') == "true"


# ---------------- teclado (necesita permiso de Accesibilidad) ----------------
def activate(app):
    osa(f"tell application {_q(app)} to activate"); time.sleep(0.6)


def keystroke(text):
    osa(f'tell application "System Events" to keystroke {_q(text)}')


def key(k, mods=()):
    """k: letra o 'return'/'escape'/'down'/'delete'; mods: ('command','shift',...)"""
    codes = {"return": 36, "escape": 53, "down": 125, "up": 126, "delete": 51, "tab": 48}
    using = (" using {" + ", ".join(m + " down" for m in mods) + "}") if mods else ""
    if k in codes: osa(f'tell application "System Events" to key code {codes[k]}{using}')
    else: osa(f'tell application "System Events" to keystroke {_q(k)}{using}')


def paste_file(path):
    saved = clipboard_get()
    clipboard_set_file(path); time.sleep(0.3); key("v", ("command",)); time.sleep(2.5)
    if saved.strip(): clipboard_set_text(saved)


def chat_prepare(app, search_keys, recipient, text, file=None):
    """Slack/WhatsApp: abre el chat, adjunta y escribe SIN enviar."""
    if not is_running(app): open_uri({"Slack": "slack://open", "WhatsApp": "whatsapp://"}.get(app, "")) ; time.sleep(4)
    activate(app)
    key(*search_keys); time.sleep(0.7)
    key("a", ("command",)); key("delete")
    keystroke(recipient); time.sleep(1.8)
    if app == "WhatsApp": key("down"); time.sleep(0.3)
    key("return"); time.sleep(1.8)
    if file: paste_file(file)
    if text: keystroke(text)
    return True


def chat_send(app):
    activate(app); key("return")


def chat_discard(app):
    activate(app); key("escape"); key("a", ("command",)); key("delete")


# ---------------- Granola ----------------
def granola_capturing():
    """¿Granola está grabando? En Mac, Granola muestra una ventanita flotante mientras graba:
    la busco en la lista de ventanas del sistema (no necesita permisos)."""
    try:
        from Quartz import CGWindowListCopyWindowInfo, kCGWindowListOptionOnScreenOnly, kCGNullWindowID
    except ImportError:
        return False
    for w in CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []:
        if w.get("kCGWindowOwnerName") != "Granola": continue
        b = w.get("kCGWindowBounds") or {}
        if w.get("kCGWindowLayer", 0) > 0 and b.get("Width", 999) < 200 and b.get("Height", 999) < 200:
            return True   # la "burbuja" flotante de grabación
    return False


def granola_click(label):
    """Aprieta un botón de Granola por su nombre (Accesibilidad)."""
    return osa(f'tell application "System Events" to tell process "Granola" to click '
               f'(first button of entire contents of front window whose description is {_q(label)} or name is {_q(label)})') != ""


def granola_text():
    """Todo el texto visible de la ventana de Granola (para leer la nota abierta)."""
    return osa('tell application "System Events" to tell process "Granola" to '
               'get value of (every static text of entire contents of front window)', 20)


# ---------------- cursor y ventana ----------------
def cursor_pos():
    """Posición del mouse en coordenadas de pantalla con origen arriba a la izquierda."""
    from AppKit import NSEvent, NSScreen
    p = NSEvent.mouseLocation(); h = NSScreen.mainScreen().frame().size.height
    return p.x, h - p.y


def screen_width():
    from AppKit import NSScreen
    return int(NSScreen.mainScreen().frame().size.width)


def hide_from_dock():
    """Que Mate no aparezca en el Dock ni en Cmd+Tab (como la barra de menú)."""
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().setActivationPolicy_(1)   # Accessory
    except Exception:
        pass


def take_focus():
    try:
        from AppKit import NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        pass
