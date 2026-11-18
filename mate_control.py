"""Control de apps por dentro: apretar botones, escribir y usar atajos en otras aplicaciones.

Usa la accesibilidad de Windows (UI Automation). Las apps hechas con Electron/Chromium
(Spotify, Granola, Slack, WhatsApp, Discord…) solo muestran sus botones si se abren con
--force-renderer-accessibility, así que Mate las abre (o reabre) con ese modo cuando hace falta.
"""
import difflib, os, re, subprocess, time, urllib.parse
from pathlib import Path

import psutil

FLAG = "--force-renderer-accessibility"
APPDATA, LOCAL = Path(os.environ.get("APPDATA", "")), Path(os.environ.get("LOCALAPPDATA", ""))
KNOWN = {   # apps Electron conocidas: nombre de proceso -> ejecutable
    "spotify": APPDATA / "Spotify" / "Spotify.exe",
    "granola": LOCAL / "Programs" / "@granolaelectron" / "Granola.exe",
    "discord": None, "slack": None, "whatsapp": None,
}


def _procs(name):
    return [p for p in psutil.process_iter(["name", "cmdline", "exe"])
            if (p.info["name"] or "").lower().startswith(name.lower())]


def _desktop():
    from pywinauto import Desktop
    return Desktop(backend="uia")


def windows_of(name):
    pids = {p.pid for p in _procs(name)}
    return [w for w in _desktop().windows() if w.element_info.process_id in pids and w.window_text()]


def ensure_accessible(name, exe=None, extra_args=(), restart_ok=True):
    """Deja la app abierta con accesibilidad. Devuelve True si tuvo que (re)abrirla."""
    exe = exe or KNOWN.get(name)
    procs = _procs(name)
    has_flag = any(FLAG in " ".join(p.info["cmdline"] or []) for p in procs)
    if procs and has_flag:
        return False
    if procs and not exe:
        exe = Path(procs[0].info["exe"])
    if procs and restart_ok:
        for p in procs:
            try: p.kill()
            except Exception: pass
        time.sleep(1.5)
    if exe and Path(exe).exists():
        subprocess.Popen([str(exe), FLAG, *extra_args], creationflags=0x08000000)
        return True
    return False


def find_buttons(win, pattern, types=("Button", "ListItem", "Hyperlink", "MenuItem")):
    rx = re.compile(pattern, re.I)
    out = []
    for e in win.descendants():
        try:
            if e.element_info.control_type in types and rx.search(e.window_text() or ""):
                out.append(e)
        except Exception:
            pass
    return out


def press(el):
    try: el.invoke()
    except Exception: el.click_input()


def wait_for(fn, timeout=10, step=0.5):
    t = time.time()
    while time.time() - t < timeout:
        try:
            r = fn()
            if r: return r
        except Exception:
            pass
        time.sleep(step)
    return None


# =========================== Spotify ===========================
def clean_music_query(q):
    """'una canción de Serati en Spotify' -> 'Serati'."""
    q = re.sub(r"\b(en|por|de|desde)\s+spotify\b", " ", q, flags=re.I)
    q = re.sub(r"^\s*(pon[eé]|reproduc[ií]|tir[aá]|met[eé])\s+", " ", q, flags=re.I)
    q = re.sub(r"^\s*(una|un|alguna|algún|unas|unos|el|la|los|las)?\s*(canci[oó]n|tema|temas|m[uú]sica|disco|[aá]lbum|playlist|lista)\s+((de|del|para)\s+)?",
               " ", q, flags=re.I)
    q = re.sub(r"^\s*algo\s+(de|del)\s+", " ", q, flags=re.I)
    return re.sub(r"\s+", " ", q).strip(" .,!?¿¡") or q.strip()


def _restore_focus(prev, spotify_was_visible):
    """Devuelve el foco a lo que estabas usando y, si Spotify estaba escondido, lo vuelve a esconder."""
    u = ctypes_user32()
    for w in windows_of("spotify"):
        try:
            h = w.handle
            if not spotify_was_visible: u.ShowWindow(h, 7)     # minimizar sin activar
        except Exception:
            pass
    if prev and u.IsWindow(prev):
        k32 = __import__("ctypes").windll.kernel32
        for _ in range(3):
            fg = u.GetForegroundWindow()
            if fg == prev: break
            a, b = u.GetWindowThreadProcessId(fg, None), k32.GetCurrentThreadId()
            u.AttachThreadInput(b, a, True)
            u.keybd_event(0x12, 0, 0, 0); u.keybd_event(0x12, 0, 2, 0)      # Alt: Windows permite cambiar el foco
            if u.IsIconic(prev): u.ShowWindow(prev, 9)
            u.BringWindowToTop(prev); u.SetForegroundWindow(prev)
            u.AttachThreadInput(b, a, False)
            time.sleep(0.15)


def ctypes_user32():
    return __import__("ctypes").windll.user32


def spotify_play(query):
    """Busca en Spotify y reproduce lo más parecido, sin sacarte de lo que estás haciendo."""
    u = ctypes_user32()
    prev = u.GetForegroundWindow()
    ws0 = windows_of("spotify")
    spotify_was_visible = bool(ws0) and any(u.IsWindowVisible(w.handle) and not u.IsIconic(w.handle) for w in ws0) \
        and u.GetForegroundWindow() in [w.handle for w in ws0]
    query = clean_music_query(query)
    reopened = ensure_accessible("spotify", extra_args=(f"--uri=spotify:search:{query}",))
    if reopened:
        time.sleep(6)
    os.startfile("spotify:search:" + urllib.parse.quote(query))
    time.sleep(2.2)   # deja que se vaya la página anterior
    q = query.lower()
    words = [w for w in re.findall(r"\w+", q) if len(w) > 2]
    name_of = lambda b: re.sub(r"^(Reproducir|Play)\s+", "", b.window_text(), flags=re.I)

    def word_hit(w, n):   # tolera errores de dictado: "serati" ~ "cerati"
        return w in n or any(difflib.SequenceMatcher(None, w, x).ratio() >= 0.75 for x in re.findall(r"\w+", n))

    def score(n):
        n = n.lower()
        hits = sum(word_hit(w, n) for w in words) / max(1, len(words))
        return hits + difflib.SequenceMatcher(None, q, n).ratio() * 0.5

    state = {"btns": []}
    def candidates():   # espera a que carguen resultados que se parezcan a lo pedido
        ws = windows_of("spotify")
        if not ws: return None
        btns = find_buttons(ws[0], r"^(Reproducir|Play) ")
        state["btns"] = btns
        good = [b for b in btns if score(name_of(b)) >= 0.95]
        return good or None
    btns = wait_for(candidates, timeout=10, step=0.7)
    if not btns:   # sin coincidencia clara: el resultado principal (el primero de la búsqueda)
        cand = [b for b in state["btns"] if score(name_of(b)) >= 0.45]
        btns = cand[:1] or state["btns"][:1]
    if not btns:
        _restore_focus(prev, spotify_was_visible)
        return f"Busqué '{query}' en Spotify pero no encontré qué reproducir"
    best = max(btns, key=lambda b: score(name_of(b)) - btns.index(b) * 0.01)  # empate: el primero
    label = name_of(best)
    press(best)
    time.sleep(0.6)
    _restore_focus(prev, spotify_was_visible)
    return f"♫ {label}"


# =========================== Granola ===========================
def granola_record():
    """Abre Granola y crea una nota nueva: Granola empieza a transcribir solo."""
    reopened = ensure_accessible("granola")
    exe = KNOWN["granola"]
    if not reopened and exe.exists():
        subprocess.Popen([str(exe), FLAG], creationflags=0x08000000)   # si está en la bandeja, muestra la ventana
    def new_note():
        ws = windows_of("granola")
        for w in ws:
            b = find_buttons(w, r"^New note$")
            if b: return b[0]
            back = find_buttons(w, r"^(Back|Home)$")      # está dentro de una nota: vuelvo al inicio
            if back: press(back[0]); time.sleep(1)
    b = wait_for(new_note, timeout=20)
    if not b:
        return "✗ Abrí Granola pero no encontré el botón de nota nueva"
    press(b)
    return "✓ Granola está grabando una nota nueva"


# =========================== genérico ===========================
def app_click(app, label):
    """Aprieta un botón por su nombre en cualquier app (abriéndola con accesibilidad si hace falta)."""
    name = app.lower().split()[0]
    if name in KNOWN: ensure_accessible(name, restart_ok=False)
    ws = wait_for(lambda: windows_of(name), timeout=10)
    if not ws: return f"✗ No encontré la app {app} abierta"
    all_btns = find_buttons(ws[0], r".+")
    labels = [b.window_text() for b in all_btns]
    hit = difflib.get_close_matches(label, labels, 1, 0.45) or [l for l in labels if label.lower() in l.lower()][:1]
    if not hit: return f"✗ No encontré '{label}' en {app}"
    press(all_btns[labels.index(hit[0])])
    return f"✓ Apreté '{hit[0]}' en {app}"


def app_type(app, text=None, keys=None):
    """Escribe texto o usa un atajo (ej: '^n' = Ctrl+N, '{ENTER}') en una app."""
    from pywinauto.keyboard import send_keys
    name = app.lower().split()[0]
    ws = wait_for(lambda: windows_of(name), timeout=10)
    if not ws: return f"✗ No encontré la app {app} abierta"
    ws[0].set_focus(); time.sleep(0.3)
    if text: send_keys(text, with_spaces=True, pause=0.01)
    if keys: send_keys(keys)
    return f"✓ Listo en {app}"


def granola_capturing():
    """¿Granola está grabando? Mira si tiene el micrófono abierto y activo (no toca la ventana de Granola)."""
    import comtypes, psutil
    from comtypes import CLSCTX_ALL
    from pycaw.constants import CLSID_MMDeviceEnumerator
    from pycaw.pycaw import IMMDeviceEnumerator, IAudioSessionManager2, IAudioSessionControl2
    pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == "granola.exe"}
    if not pids: return False
    en = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_INPROC_SERVER)
    col = en.EnumAudioEndpoints(1, 1)          # micrófonos activos
    for d in range(col.GetCount()):
        mgr = col.Item(d).Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None).QueryInterface(IAudioSessionManager2)
        se = mgr.GetSessionEnumerator()
        for i in range(se.GetCount()):
            c = se.GetSession(i).QueryInterface(IAudioSessionControl2)
            if c.GetProcessId() in pids and c.GetState() == 1: return True
    return False


def granola_stop():
    """Frena la grabación: es el botón cuadrado sin nombre más a la derecha de la barra de abajo."""
    for w in windows_of("granola"):
        wr = w.rectangle()
        bottom = [e for e in w.descendants(control_type="Button")
                  if not (e.window_text() or "").strip() and e.rectangle().top > wr.top + wr.height() * 0.75
                  and e.rectangle().width() < 60]
        if len(bottom) >= 2:
            stop = max(bottom, key=lambda e: e.rectangle().left)
            w.set_focus(); time.sleep(0.2); press(stop)
            return "✓ Frené la grabación de Granola"
    return "✗ No encontré una grabación activa en Granola"


# =========================== archivos ===========================
HOME = Path.home()
SEARCH_DIRS = [HOME / "Desktop", HOME / "OneDrive" / "Escritorio", HOME / "Documents", HOME / "OneDrive" / "Documentos",
               HOME / "Downloads", HOME / "Pictures"]
EXT_WORDS = {"pdf": ".pdf", "excel": ".xlsx", "planilla": ".xlsx", "word": ".docx", "documento": ".docx",
             "presentación": ".pptx", "presentacion": ".pptx", "deck": ".pptx", "foto": ".jpg", "imagen": ".png",
             "captura": ".png", "zip": ".zip", "video": ".mp4"}


def find_file(desc, limit=5):
    """Busca un archivo por descripción ("el pdf del presupuesto de Cabrini") en las carpetas habituales."""
    words = [w for w in re.findall(r"\w+", desc.lower()) if len(w) > 2 and w not in ("del", "las", "los", "una", "archivo")]
    want_ext = {EXT_WORDS[w] for w in words if w in EXT_WORDS}
    words = [w for w in words if w not in EXT_WORDS]
    found = []
    for base in SEARCH_DIRS:
        if not base.exists(): continue
        for depth, pattern in ((0, "*"), (1, "*/*"), (2, "*/*/*")):
            for f in base.glob(pattern):
                if not f.is_file() or f.name.startswith("~$"): continue
                if want_ext and f.suffix.lower() not in want_ext and not (".xls" in want_ext and f.suffix.lower() == ".xls"):
                    continue
                name = f.stem.lower().replace("_", " ").replace("-", " ")
                hits = sum(w in name for w in words)
                if hits or (not words and want_ext):
                    found.append((hits / max(1, len(words)) - depth * 0.05, f.stat().st_mtime, f))
    found.sort(key=lambda x: (-x[0], -x[1]))
    return [f for _, _, f in found[:limit]]


# =========================== Slack ===========================
def _slack_window():
    ws = windows_of("slack")
    return ws[0] if ws else None


def slack_prepare(recipient, text, file=None):
    """Abre Slack, va al chat de la persona/canal, adjunta el archivo y escribe el mensaje SIN enviarlo."""
    from pywinauto.keyboard import send_keys
    w = _slack_window()
    if not w:
        os.startfile("slack://open"); w = wait_for(_slack_window, timeout=20)
    if not w: return False, "✗ No pude abrir Slack"
    w.set_focus(); time.sleep(0.6)
    send_keys("^k"); time.sleep(0.8)                         # buscador rápido
    send_keys(recipient.replace(" ", "{SPACE}"), pause=0.03); time.sleep(1.5)
    send_keys("{ENTER}"); time.sleep(2)
    if file:   # copio el archivo al portapapeles, lo pego en el mensaje y después te devuelvo tu portapapeles
        ps = lambda cmd: subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True,
                                        text=True, creationflags=0x08000000)
        saved = ps("Get-Clipboard -Raw").stdout
        safe = str(file).replace("'", "''")
        ps(f"Set-Clipboard -LiteralPath '{safe}'")
        time.sleep(0.4); send_keys("^v"); time.sleep(3)
        if saved.strip():
            subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
                           input=saved.rstrip("\r\n"), text=True, creationflags=0x08000000)
    if text:
        send_keys(text.replace(" ", "{SPACE}").replace("\n", "+{ENTER}")
                  .replace("(", "{(}").replace(")", "{)}").replace("+", "{+}").replace("%", "{%}").replace("^", "{^}")
                  .replace("{{+}}", "{+}"), pause=0.01)
    return True, "listo para enviar"


def slack_send_now():
    from pywinauto.keyboard import send_keys
    w = _slack_window()
    if not w: return "✗ Slack no está abierto"
    w.set_focus(); time.sleep(0.3); send_keys("{ENTER}")
    return "✓ Mensaje enviado por Slack"


def slack_discard():
    """Borra lo que quedó escrito (y el adjunto) si cancelás."""
    from pywinauto.keyboard import send_keys
    w = _slack_window()
    if not w: return
    w.set_focus(); time.sleep(0.3); send_keys("^a{BACKSPACE}"); time.sleep(0.3)
    for b in find_buttons(w, r"^(Remove|Quitar|Eliminar)"): press(b)


def _granola_home(w):
    """Vuelve a la pantalla de inicio de Granola (donde está la lista de notas)."""
    for _ in range(3):
        if find_buttons(w, r"^New note$") and not find_buttons(w, r"^Back$"):
            return True
        b = find_buttons(w, r"^(Back|Home)$")
        if b: press(b[0])
        time.sleep(1.5)
    return bool(find_buttons(w, r"^New note$"))


def granola_read_note(title_hint=None, tries=3):
    """Lee una nota de Granola (la más reciente con contenido, o la que coincida con el título)
    y después te devuelve el foco a lo que estabas usando."""
    u = ctypes_user32(); prev = u.GetForegroundWindow()
    try:
        return _granola_read_note(title_hint, tries)
    finally:
        _restore_focus(prev, True)


def _granola_read_note(title_hint=None, tries=3):
    exe = KNOWN["granola"]
    if not windows_of("granola") and exe.exists():
        subprocess.Popen([str(exe), FLAG], creationflags=0x08000000)
    for attempt in range(tries):
        w = wait_for(lambda: (windows_of("granola") or [None])[0], timeout=20)
        if not w: return None, None
        try:
            w.set_focus(); time.sleep(0.8)
            _granola_home(w)
            notes = wait_for(lambda: [e for e in w.descendants(control_type="ListItem")
                                      if (e.window_text() or "").strip()], timeout=10) or []
            real = [n for n in notes if not n.window_text().startswith("New note")]
            if title_hint:
                names = [n.window_text().lower() for n in real]
                hit = [x for x in names if title_hint.lower() in x] or difflib.get_close_matches(title_hint.lower(), names, 1, 0.5)
                if not hit: return None, None      # no hay una reunión con ese nombre: no adivino
                real = [real[names.index(hit[0])]]
            if not real: continue
            item = real[0].window_text()
            btns = [e for e in w.descendants(control_type="Button")
                    if (e.window_text() or "").strip() and item.startswith(e.window_text().strip())
                    and not e.window_text().startswith(("Select", "Note options", "New note"))]
            if not btns: continue
            b = max(btns, key=lambda e: len(e.window_text()))
            title = b.window_text()
            press(b)
            if not wait_for(lambda: find_buttons(w, r"^Back$"), timeout=10): continue
            time.sleep(2.5)
            texts = []
            for e in w.descendants():
                t = (e.window_text() or "").strip().replace("￼", "")
                if e.element_info.control_type in ("Text", "Edit", "Document", "ListItem", "Group") and len(t) > 2 and t not in texts:
                    texts.append(t)
            body = chr(10).join(texts)
            if len(body) > 200: return title, body
        except Exception:
            time.sleep(1.5)
    return None, None


def granola_generate_notes(timeout=180):
    """Aprieta 'Generate notes' en la nota abierta y espera a que Granola termine de escribirlas."""
    w = (windows_of("granola") or [None])[0]
    if not w: return False
    w.set_focus(); time.sleep(0.5)
    g = find_buttons(w, r"^Generate notes$")
    if g: press(g[0])
    last, stable, t0 = -1, 0, time.time()
    while time.time() - t0 < timeout:
        time.sleep(4)
        n = sum(len(e.window_text() or "") for e in w.descendants(control_type="Text"))
        stable = stable + 1 if n == last and n > 300 else 0
        last = n
        if stable >= 2: return True          # dejó de escribir
    return last > 300


def _note_texts(w):
    texts = []
    for e in w.descendants():
        t = (e.window_text() or "").strip().replace("\ufffc", "")
        if e.element_info.control_type in ("Text", "Edit", "Document", "ListItem", "Group") and len(t) > 2 and t not in texts:
            texts.append(t)
    return texts


def granola_read_current():
    """Lee la nota que Granola tiene abierta ahora (la reunión en curso), sin navegar."""
    u = ctypes_user32(); prev = u.GetForegroundWindow()
    try:
        ws = windows_of("granola")
        if not ws: return None, None
        w = ws[0]
        w.set_focus(); time.sleep(0.8)
        if not find_buttons(w, r"^Back$"):          # no hay una nota abierta: uso la más reciente
            return granola_read_note()
        texts = [t for t in _note_texts(w) if t not in ("Granola", "Ask anything", "Write notes, or press '/' for templates")]
        title = next((t for t in texts if 3 < len(t) < 90), "Reunión en curso")
        return title, chr(10).join(texts)
    finally:
        _restore_focus(prev, True)
