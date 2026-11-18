"""Interfaz moderna de Mate: ventana web (WebView2) con forma de notch arriba de la pantalla
que se despliega en un panel. Corre todo local; el HTML está en ui/mate.html."""
import ctypes, datetime as dt, json, subprocess, sys, threading, time, uuid
from ctypes import wintypes
from pathlib import Path

import webview
import mate_mac as mac

HERE = Path(__file__).parent
IS_MAC = mac.IS_MAC
if not IS_MAC:
    user32, gdi32, dwm = ctypes.windll.user32, ctypes.windll.gdi32, ctypes.windll.dwmapi


def _screen():
    """Ancho de pantalla en píxeles lógicos (sin escalado de Windows)."""
    if IS_MAC: return mac.screen_width()
    return user32.GetSystemMetrics(0) / (user32.GetDpiForSystem() / 96)


ISLA = {   # Cebadura: el Mate al centro; cada estado tiene su tamaño
    "idle": (88, 28), "ok": (250, 30), "": (280, 30), "alert": (320, 30), "listen": (250, 34),
    "think": (300, 34), "music": (300, 30), "focus": (270, 30), "rec": (270, 30), "undo": (300, 30),
    "card": (450, 150),
}


def sizes():
    """Isla en reposo, isla con mensaje y panel (este último se adapta a pantallas chicas)."""
    sw = _screen()
    panel = (int(min(860, sw * 0.64)), int(min(530, sw * 0.40)))
    return ISLA["idle"], ISLA[""], panel


class WebUI:
    """Implementa lo que el Agent le pide a la interfaz (confirm, alert, mail_dialog, ...)."""

    def __init__(self, agent_cls, speaker_cls, listener_cls, cfg, wake, name):
        self.cfg, self.wake, self.name = cfg, wake, name
        self.expanded, self.busy, self.ready = False, False, False
        self.pending = {}            # modales esperando respuesta
        self.history = []            # chat visible
        self.mails, self.unread = [], None
        self.api = Api(self)
        self.size_idle, self.size_wide, self.size_panel = sizes()
        self.wide_until, self.cur = 0, self.size_idle
        self.size_drop = (460, 250)
        self.dropping, self.drop_paths, self.media_now = False, [], None
        self.card_on, self.recording, self.undo_ev = False, None, None
        self.events, self.out = [], {}
        w, h = self.size_idle
        self.window = webview.create_window(
            name, str(HERE / "ui" / "mate.html"), js_api=self.api, width=w, height=h,
            x=int((_screen() - w) / 2), y=0, frameless=True, on_top=True, resizable=False,
            background_color="#000000", easy_drag=False, focus=True, min_size=(60, 16),
            transparent=IS_MAC)   # en Mac la forma redondeada la dibuja la página (fondo transparente)
        self.speaker = speaker_cls(cfg.get("voice_id"), cfg.get("voice_speed", 1.0))
        self.agent = agent_cls(self)
        names = " ".join(w for m in self.agent.d.get("memory", []) for w in m.split() if w[:1].isupper())
        def _voice(t):
            self._from_voice = True; self.run(t)
        self.listener = listener_cls(wake, _voice, self.listening, self.speaker,
                                     clap_threshold=cfg.get("clap_threshold", 0.55),
                                     whisper_model=cfg.get("whisper", "small"), vocab=names,
                                     claps=cfg.get("claps", False))   # aplausos: apagados salvo que los prendas
        self.listener.in_meeting = self.in_meeting
        self.listener.on_audio = self._on_audio
        self.listener.on_heard = lambda t: self.notch("No te entendí, probá de nuevo", "", 3)
        self.listener.on_wake_hint = lambda: (self.call("mate", "listening", 4), self.notch("Te escucho…", "listen", 4))
        self.voice_err = self.listener.start() if cfg.get("voice", True) else "voz apagada"
        self.window.events.shown += self._shape
        self.window.events.closed += lambda: None

    # ---------------- ventana ----------------
    def hwnd(self):
        try: return int(str(self.window.native.Handle))
        except Exception: return user32.FindWindowW(None, self.name)

    def _shape(self, *_):
        """Recorta la ventana con esquinas redondeadas (abajo) para que parezca un notch/isla."""
        time.sleep(0.15)
        if IS_MAC:   # sin ícono en el Dock; las esquinas las redondea el CSS (body.mac)
            mac.hide_from_dock(); return
        h = self.hwnd()
        if not getattr(self, "_hidden_taskbar", False):  # que no aparezca en la barra de tareas
            ex = user32.GetWindowLongW(h, -20)
            user32.ShowWindow(h, 0)
            user32.SetWindowLongW(h, -20, (ex | 0x80) & ~0x40000)   # WS_EX_TOOLWINDOW, sin APPWINDOW
            user32.ShowWindow(h, 4)
            self._hidden_taskbar = True
        # Esquinas redondeadas nativas de Windows 11 (suavizadas). El recorte con SetWindowRgn dejaba
        # las esquinas grises porque WebView2 no respeta la región al dibujar.
        try:
            user32.SetWindowRgn(h, None, True)
            dwm.DwmSetWindowAttribute(h, 34, ctypes.byref(ctypes.c_uint(0xFFFFFFFE)), 4)  # sin borde
            dwm.DwmSetWindowAttribute(h, 33, ctypes.byref(ctypes.c_uint(2)), 4)           # DWMWCP_ROUND
        except Exception: pass

    def resize(self, size):
        if size == self.cur: return
        self.cur = size
        self.window.resize(*size)
        self.window.move(int((_screen() - size[0]) / 2), 0)
        threading.Thread(target=self._shape, daemon=True).start()

    def expand(self):
        if self.expanded: return
        self.expanded = True
        self.js("document.body.classList.add('open')")
        self.resize(self.size_panel)
        self.push()
        threading.Thread(target=self.take_focus, daemon=True).start()
        self.js("setTimeout(()=>document.getElementById('q').focus(),80)")

    def take_focus(self):
        """Windows no deja robar el foco fácil: se 'engancha' al hilo de la ventana activa."""
        time.sleep(0.25)
        if IS_MAC:
            mac.take_focus()
            if self.expanded: self.js("document.getElementById('q').focus()")
            return
        h, fg = self.hwnd(), user32.GetForegroundWindow()
        if fg == h: return
        k32 = ctypes.windll.kernel32
        a, b = user32.GetWindowThreadProcessId(fg, None), k32.GetCurrentThreadId()
        if user32.GetAsyncKeyState(0x01) & 0x8000: return   # arrastrando algo con el mouse: no tocar el foco
        user32.AttachThreadInput(b, a, True)
        user32.keybd_event(0x12, 0, 0, 0); user32.keybd_event(0x12, 0, 2, 0)  # Alt: habilita el cambio de foco
        user32.BringWindowToTop(h); user32.SetForegroundWindow(h); user32.SetFocus(h)
        user32.AttachThreadInput(b, a, False)
        if self.expanded: self.js("document.getElementById('q').focus()")

    def _cursor_watch(self):
        """Los ojos de Mate siguen el mouse por toda la pantalla (no sólo arriba de la isla).
        Sólo le avisa a la página cuando cambia hacia dónde mirar, así no gasta nada."""
        last, pt, r = None, wintypes.POINT(), wintypes.RECT()
        while True:
            time.sleep(0.08)
            try:
                if not self.ready: continue
                if IS_MAC:
                    px, _ = mac.cursor_pos()
                    left = (_screen() - self.cur[0]) / 2; right = left + self.cur[0]
                else:
                    user32.GetCursorPos(ctypes.byref(pt)); user32.GetWindowRect(self.hwnd(), ctypes.byref(r))
                    px, left, right = pt.x, r.left, r.right
                cx = (left + right) / 2
                if self.expanded: cx = left + 125          # con el panel abierto Mate está arriba a la izquierda
                dx = px - cx
                v = -1 if dx < -40 else 1 if dx > 40 else 0
                if v != last:
                    last = v; self.js(f"eyeX={v}")
            except Exception:
                pass

    def _esc_watch(self):
        """Esc achica la isla aunque el foco esté en otro lado del panel."""
        if IS_MAC: return   # en Mac la ventana recibe la tecla y la maneja la página
        while True:
            time.sleep(0.05)
            if self.expanded and user32.GetAsyncKeyState(0x1B) & 0x8000                     and user32.GetForegroundWindow() == self.hwnd():
                if self.pending or self.card_on: continue
                self.collapse(); time.sleep(0.4)

    def collapse(self):
        if not self.expanded or self.pending: return
        self.expanded, self.dropping, self.drop_paths = False, False, []
        self.resize(self.size_idle)
        self.js("document.body.classList.remove('open','dropping'); dropFiles=[]")

    def js(self, code):
        if not self.ready: return
        try: self.window.evaluate_js(code)
        except Exception: pass

    def notch(self, text, kind="", secs=4):
        self.call("status", text, kind, secs)
        if self.expanded or self.card_on: return
        self.wide_until = time.time() + secs
        base_w, h = ISLA.get(kind, ISLA[""])
        extra = 70 if kind in ("ok", "think") else 40          # lo de la derecha (hora, segundos)
        txt = len(text.split("\n")[0][:60])
        w = max(base_w, min(420, 2 * (txt * 7 + 30) + 28 + extra // 2))   # el Mate queda al centro
        self.resize((int(w), h))

    def island_size(self):
        """Tamaño que corresponde cuando no hay mensaje: foco > grabación > música > reposo."""
        if self.agent.focus_end: return ISLA["focus"]
        if self.recording: return ISLA["rec"]
        if self.media_now and self.media_now.get("playing"): return ISLA["music"]
        return ISLA["idle"]

    def _shrink_check(self):
        if not self.expanded and not self.card_on and not self.dropping and time.time() > self.wide_until:
            self.resize(self.island_size())

    def step(self, text):
        """Paso en vivo mientras piensa (se ve en la conversación y en la isla)."""
        self.call("step", text)
        if not self.expanded and not self.card_on:
            self.wide_until = time.time() + 120; self.resize(ISLA["think"])

    def undo_window(self, label, secs=5):
        """Muestra 'Mandando… [Deshacer]' unos segundos. True = seguir; False = lo cancelaste."""
        self.undo_ev = threading.Event()
        if not self.expanded:
            self.card_on = True; self.resize(ISLA["undo"])
        self.call("undo", label, secs)
        cancelled = self.undo_ev.wait(secs)
        self.card_on = False; self.wide_until = 0
        if cancelled: self.notch("Cancelado, no mandé nada", "", 3)
        return not cancelled

    def call(self, fn, *args):
        self.js(f"Mate.{fn}(" + ",".join(json.dumps(a, ensure_ascii=False) for a in args) + ")")

    # ---------------- estado para la interfaz ----------------
    def snapshot(self):
        a, d = self.agent, self.agent.d
        today = dt.date.today()
        habits = []
        for n, h in d["habits"].items():
            streak, day = 0, today if h["log"].get(today.isoformat(), 0) >= h["goal"] else today - dt.timedelta(days=1)
            while h["log"].get(day.isoformat(), 0) >= h["goal"]:
                streak += 1; day -= dt.timedelta(days=1)
            habits.append({"name": n, "goal": h["goal"], "today": h["log"].get(today.isoformat(), 0), "streak": streak})
        rem = sorted(d["reminders"], key=lambda r: r["ts"])
        focus = None
        if a.focus_end:
            focus = {"left": max(0, int(a.focus_end - time.time())), "total": int(a.focus_total), "brk": a.focus_break}
        log = d.get("focus_log", {})
        stats = []
        for i in range(6, -1, -1):
            day = today - dt.timedelta(days=i)
            stats.append({"d": "lmmjvsd"[day.weekday()].upper(), "min": log.get(day.isoformat(), 0), "today": i == 0})
        from mate_audio import voices
        cfg = {k: self.cfg.get(k) for k in ("voice", "speak", "mail_alerts", "clap_threshold", "model", "wake_word",
                                            "claps", "voice_id", "voice_speed", "look", "expresivo", "argentinidad",
                                            "gestos", "toque", "ojos", "desktop", "mail_nivel", "tema", "ciudad")}
        cfg["voices"] = voices()
        cfg["gemini_on"] = bool((self.cfg.get("gemini_key") or "").strip())   # la clave nunca viaja a la página
        cfg["granola_on"] = bool((self.cfg.get("granola_key") or "").strip())
        return {"tasks": d["tasks"], "habits": habits, "memory": d["memory"],
                "reminders": [{"time": dt.datetime.fromtimestamp(r["ts"]).strftime("%H:%M"), "text": r["text"]} for r in rem],
                "focus": focus, "focus_stats": stats, "accounts": a.gmail.accounts(),
                "accounts_info": [{"alias": x, "email": a.gmail.label(x)} for x in a.gmail.accounts()],
                "spotify_on": bool(getattr(a, "spotify", None) and a.spotify.sp),
                "connecting": getattr(self, "connecting", False),
                "events": self.events_view(), "out": self.out, "recording": self.recording,
                "mails": [{k: m[k] for k in ("account", "from", "subject", "snippet")} for m in self.mails],
                "unread": self.unread, "cfg": cfg, "busy": self.busy, "history": self.history[-20:],
                "name": self.cfg.get("user_name", ""), "voice": not self.voice_err, "media": self.media_now}

    def push(self):
        self.call("state", self.snapshot())

    # ---------------- lo que usa el Agent ----------------
    def refresh_board(self): self.push()
    def open_board(self): self.expand(); self.call("open", "semana")

    def background(self, fn, speak=True):
        def work():
            res = fn()
            self.show(res, speak)
        threading.Thread(target=work, daemon=True).start()

    def run(self, text, speak=True):
        if self.busy: return
        self.busy = True
        self.last_text = text
        self.history.append({"who": "u", "text": text})
        self.call("user", text); self.call("mate", "thinking"); self.notch("Entendiendo el pedido…", "think", 120)
        self.background(lambda: self.agent.handle(text), speak)

    def _on_audio(self, audio, by_wake):
        """Voz con Gemini. Devuelve None para que el micrófono use Whisper local (sin clave o si Gemini falla)."""
        if not self.agent.gemini_key() or self.busy: return None
        self.busy = True
        self.call("mate", "thinking"); self.notch("Entendiendo…", "think", 30)
        try:
            heard, out = self.agent.handle_audio(audio)
        except Exception as e:
            heard, out = None, None
        if heard is None:                            # Gemini no respondió: sigue con la voz local
            self.busy = False; return None
        if not heard:                                # ruido o no era para Mate
            self.busy = False
            if not by_wake: self.notch("No te entendí, probá de nuevo", "", 3)
            self.call("mate", "idle", 1)
            return True
        self.last_text = heard; self._from_voice = True
        self.history.append({"who": "u", "text": heard}); self.call("user", heard)
        self.show(out or "Listo", speak=True)
        return True

    def in_meeting(self):
        """¿Estás en una reunión? (Granola grabando o un evento del calendario que está pasando ahora)."""
        if self.recording: return True
        try: return any(e.get("in") == "ahora" for e in self.events_view())
        except Exception: return False

    def show(self, res, speak=True):
        from mate_audio import log; log("RESPUESTA:", res[:120])
        self.busy = False
        from_voice, self._from_voice = getattr(self, "_from_voice", False), False
        if self.in_meeting(): speak = False          # en reunión Mate no habla: sólo lo muestra en la isla
        ok = res.startswith(("✓", "🎉", "♫"))
        bad = res.startswith("✗")
        self.history.append({"who": "m", "text": res, "ok": ok})
        self.call("reply", res, ok)
        word = res.replace("✓", "").strip().split("\n")[0]
        self.notch(word, "ok" if ok else ("alert" if bad else ""), 5)
        self.call("mate", "happy" if ok else "error" if bad else "speaking", 3)
        self.push()
        if speak and self.cfg.get("speak", True) and res.strip() != "✓":
            self.speaker.say(res.split("\n\n(")[0].replace("\n", ". "))
        import re as _re
        cierre = _re.match(r"^\W*(gracias|muchas gracias|nada|nada m[aá]s|eso es todo|listo|chau|hasta luego)\W*$",
                           (getattr(self, "last_text", "") or "").lower())
        # Seguir escuchando sólo si VOS le hablaste recién (no después de avisos, resumen del día ni errores)
        if speak and from_voice and not bad and self.cfg.get("conversacion", True) and not self.card_on and not cierre:
            threading.Thread(target=self._follow_up, daemon=True).start()

    def _follow_up(self):
        """Modo conversación: cuando termina de hablar, sigue escuchando unos segundos sin pedir 'Mate'."""
        time.sleep(0.3)
        t0 = time.time()
        while (self.speaker.speaking or not self.speaker.q.empty()) and time.time() - t0 < 60:
            time.sleep(0.1)
        time.sleep(0.35)                     # que no se escuche la cola de su propia voz
        if not self.busy and not self.pending and self.listener.capture is None:
            self.listener.activate(follow=True)

    def listening(self, follow=False):
        """Se activó la escucha: sin hablar (si no, se pisa con lo que decís), solo se ve en la isla."""
        secs = 6 if follow else 9
        self.call("mate", "listening", secs); self.notch("Te sigo escuchando" if follow else "Te escucho", "listen", secs)
        self.call("listening", True)
        threading.Timer(secs, lambda: self.call("listening", False)).start()

    def alert(self, msg, sound=True):
        self.notch(msg, "alert", 12); self.call("mate", "alert", 4)
        self.history.append({"who": "m", "text": msg}); self.call("reply", msg, False)
        if sound:
            try:
                if IS_MAC: mac.beep()
                else: import winsound; winsound.MessageBeep(winsound.MB_ICONASTERISK)
            except Exception: pass
        if not self.in_meeting(): self.speaker.say(msg)

    def copy(self, text):
        if IS_MAC: return mac.clipboard_set_text(text)
        subprocess.run(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $input"],
                       input=text, text=True, creationflags=0x08000000)

    def _modal(self, spec):
        mid = uuid.uuid4().hex[:8]
        ev = threading.Event(); box = {}
        self.pending[mid] = (ev, box)
        if spec.get("kind") == "confirm" and not self.expanded:
            self.card_on = True
            import re as _re
            rows = len(_re.findall(r"(?mi)^\s*(Para|Mensaje|Archivo|Asunto|Cuándo|Mover|Tareas?)\s*:", spec.get("text", "")))
            self.resize((ISLA["card"][0], min(260, 128 + 22 * max(rows, 1))))
            self.call("card", dict(spec, id=mid))
            threading.Thread(target=self.take_focus, daemon=True).start()
            ev.wait()
            self.card_on = False; self.wide_until = 0
            self._shrink_check()
        else:
            self.expand()
            self.call("modal", dict(spec, id=mid))
            ev.wait()
        self.pending.pop(mid, None)
        return box.get("v")

    def confirm(self, msg, title=None, ok="Dale"):
        lines = msg.split("\n")
        if not title and lines and lines[0].rstrip().endswith("?"):
            title, msg = lines[0].strip(), "\n".join(lines[1:]).strip()
        return bool(self._modal({"kind": "confirm", "title": title or "¿Confirmás?", "text": msg, "ok": ok}))
    def ask_text(self, msg): return self._modal({"kind": "ask", "title": self.name, "text": msg})

    def mail_dialog(self, mail, draft):
        v = self._modal({"kind": "mail", "title": "Re: " + mail["subject"], "to": mail["from"],
                         "account": mail["account"], "text": draft})
        return (v["body"], v["mode"]) if v else None

    # ---------------- reloj ----------------
    def loop(self):
        last_push = 0
        while True:
            time.sleep(1)
            try:
                self.agent.tick()
                self._shrink_check()
                if time.time() - getattr(self, "_extras_t", 0) > 600:
                    self._extras_t = time.time(); threading.Thread(target=self._load_extras, daemon=True).start()
                if self.speaker.speaking: self.call("mate", "speaking", 1)
                if time.time() - last_push > 20:
                    last_push = time.time(); self.push()
            except Exception:
                pass

    # ---------------- agenda, clima, dólar y feriado (se refresca cada 10 min) ----------------
    def _load_extras(self):
        import mate_servicios as sv, mate_argento as ar
        try:
            if self.agent.gmail.accounts():
                hoy = dt.date.today()
                lunes = hoy - dt.timedelta(days=hoy.weekday())
                self.events = self.agent.gmail.events_range(lunes - dt.timedelta(days=7), lunes + dt.timedelta(days=20))
        except Exception:
            pass
        out = {}
        try:
            w = sv.weather(self.cfg.get("coords") or self.cfg.get("ciudad", ""))
            now = w[1].replace("Ahora: ", "")
            out["temp"], _, out["cielo"] = now.partition(", ")
        except Exception:
            pass
        try:
            d = [x for x in ar._json("https://dolarapi.com/v1/dolares") if x.get("casa") == "blue"]
            if d: out["blue"] = f"${d[0]['venta']:,.0f}".replace(",", ".")
        except Exception:
            pass
        try:
            hoy = dt.date.today()
            prox = sorted(f for f in list(ar.feriados(hoy.year)) + list(ar.feriados(hoy.year + 1)) if f >= hoy.isoformat())
            if prox: out["feriado"] = dt.date.fromisoformat(prox[0]).strftime("%d/%m")
        except Exception:
            pass
        self.out = out
        self.push()

    def events_view(self):
        """Eventos de ~4 semanas; marca cuáles vienen (upcoming) y cuánto falta para los de hoy."""
        now = dt.datetime.now()
        hoy = now.date().isoformat()
        out = []
        for e in self.events or []:
            item = dict(e, upcoming=False, **{"in": ""})
            d = e.get("date") or hoy
            try:
                if e.get("time", "").count(":") == 1:
                    h, m = map(int, e["time"].split(":"))
                    start = dt.datetime.combine(dt.date.fromisoformat(d), dt.time(h, m))
                else:
                    start = dt.datetime.combine(dt.date.fromisoformat(d), dt.time(23, 59))
                mins = int((start - now).total_seconds() // 60)
                if mins >= -10:
                    item["upcoming"] = True
                    if d == hoy:
                        item["in"] = "ahora" if mins <= 0 else f"en {mins} min" if mins < 90 else ""
            except ValueError:
                pass
            out.append(item)
        return out

    # ---------------- música ----------------
    def _media_watch(self):
        if IS_MAC:
            class _MacMedia:   # Spotify / Música por AppleScript
                def now(self): return mac.now_playing()
                def cmd(self, what):
                    mac.media({"playpause": "playpause", "play": "playpause", "pause": "playpause",
                               "next": "next", "prev": "prev"}.get(what, "playpause"))
            self.media = _MacMedia()
        else:
            from mate_media import Media
            self.media = Media()
        last = None
        while True:
            now = self.media.now()
            key = json.dumps(now, sort_keys=True)
            if key != last:
                last, self.media_now = key, now
                self.push()
            time.sleep(2.5)

    # ---------------- soltar archivos sobre la isla ----------------
    def _hook_drop(self):
        from webview.dom import DOMEventHandler
        doc = self.window.dom.document


        def drop(e):
            files = [f.get("pywebviewFullPath") for f in (e.get("dataTransfer", {}) or {}).get("files", [])]
            files = [f for f in files if f]
            if not files: return
            from mate_files import info
            self.drop_paths = files
            self.call("dropFiles", info(files))

        # La entrada del archivo la detecta la página al instante (ver dragenter en mate.html -> drop_enter);
        # acá sólo se necesita el drop, que es lo único que trae la ruta completa del archivo.
        doc.events.drop += DOMEventHandler(drop, True, True)

    def drop_enter(self):
        if self.dropping: return
        self.dropping = True
        self.expanded = True
        self.resize(self.size_drop)
        self.call("dropStart")

    def drop_close(self):
        self.dropping = False
        self.expanded = True
        self.collapse_force()

    def collapse_force(self):
        self.expanded = False
        self.resize(self.size_idle)
        self.js("document.body.classList.remove('open','dropping')")

    def file_action(self, action, paths, extra=""):
        import mate_files as F, mate_control as C
        import sys   # mate.py corre como __main__: importarlo de nuevo arrancaría otro Mate y lo trababa
        ask_long = sys.modules["__main__"].ask_long
        res, done = "", True
        try:
            if action == "analyze":
                outs = []
                for p in paths:
                    k = F.kind(p)
                    if k == "imagen":
                        outs.append(f"{Path(p).name}: {F.describe_image(p)}")
                        continue
                    txt = F.read_text(p)
                    if not txt.strip():
                        outs.append(f"{Path(p).name}: no pude leer texto de este archivo"); continue
                    self.call("dropResult", f"Leyendo {Path(p).name}… (puede tardar un poco)", False)
                    outs.append(f"{Path(p).name}:\n" + ask_long(
                        f"Analizá este archivo ({k}) y contame en 4 o 5 viñetas de qué se trata, lo importante y si hay algo para hacer:\n\n{txt}"))
                res = "\n\n".join(outs)
            elif action == "organize":
                res = "✓ Ordenado:\n" + "\n".join(f"{Path(p).name} → {F.organize(p).parent}" for p in paths)
            elif action == "convert":
                self.call("dropResult", f"Convirtiendo a {extra}…", False)
                res = "✓ Listo, lo guardé al lado del original:\n" + "\n".join(
                    f"{F.convert(p, extra).name}" for p in paths if extra in F.formats(p))
            elif action == "whatsapp":
                to, _, msg = (extra or "").partition(":")
                ok, m = C.whatsapp_prepare(to.strip(), msg.strip(), paths[0])
                if not ok: res = m
                elif self.confirm(f"¿Lo envío por WhatsApp?\n\nPara: {to.strip()}\nMensaje: {msg.strip() or '(sin texto)'}\nArchivo: {Path(paths[0]).name}"):
                    res = C.whatsapp_send_now()
                else:
                    C.whatsapp_discard(); res = "Ok, no lo mandé"
            elif action == "trash":
                for p in paths: F.trash(p)
                res = f"✓ Mandé {len(paths)} archivo(s) a la papelera. Si te arrepentís, están ahí."
            elif action == "slack":
                to, _, msg = (extra or "").partition(":")
                ok, m = C.slack_prepare(to.strip(), msg.strip(), paths[0])
                if not ok: res = m
                elif self.confirm(f"¿Lo envío por Slack?\n\nPara: {to.strip()}\nMensaje: {msg.strip() or '(sin texto)'}\nArchivo: {Path(paths[0]).name}"):
                    res = C.slack_send_now()
                else:
                    C.slack_discard(); res = "Ok, no lo mandé"
        except Exception as e:
            res = f"✗ Uy, algo falló: {e}"
        self.call("dropResult", res, done)
        self.call("mate", "happy" if res.startswith("✓") else "speaking", 2)

    def start(self):
        def on_ready():
            self.ready = True
            self.push()
            msg = f"Decí «{self.wake.capitalize()}…» o aplaudí dos veces" if not self.voice_err else self.voice_err
            self.notch(msg, "", 6)
            threading.Thread(target=self.loop, daemon=True).start()
            threading.Thread(target=self._esc_watch, daemon=True).start()
            threading.Thread(target=self._cursor_watch, daemon=True).start()
            threading.Thread(target=self._media_watch, daemon=True).start()
            self._hook_drop()
        self.api._on_ready = on_ready
        store = Path.home() / ".mate" / "webview"
        import shutil   # que nunca use una versión vieja de la interfaz guardada en caché
        for sub in ("Cache", "Code Cache", "GPUCache", "Service Worker"):
            for d in store.glob(f"**/{sub}"):
                shutil.rmtree(d, ignore_errors=True)
        webview.start(private_mode=False, storage_path=str(store))


class Api:
    """Funciones que el HTML puede llamar (window.pywebview.api.*)."""

    def __init__(self, ui): self._ui, self._on_ready = ui, None

    def ready(self):
        if IS_MAC: threading.Thread(target=lambda: self._ui.js("document.body.classList.add('mac')"), daemon=True).start()
        if self._on_ready: self._on_ready(); self._on_ready = None

    def expand(self): self._ui.expand()
    def collapse(self): self._ui.collapse()
    def ask(self, text): self._ui.run(text, speak=False)
    def listen(self): self._ui.listener.activate()

    def answer(self, mid, value):
        p = self._ui.pending.get(mid)
        if p: p[1]["v"] = value; p[0].set()

    def task_status(self, tid, status):
        a = self._ui.agent
        for t in a.d["tasks"]:
            if t["id"] == tid: t["status"] = status
        a.save(); self._ui.push()

    def task_shift(self, tid, days=1):
        a = self._ui.agent; hoy = dt.date.today()
        for t in a.d["tasks"]:
            if t["id"] == tid:
                d = dt.date.fromisoformat(t["date"]) if t["date"] else hoy
                if days == 0: t["date"] = hoy.isoformat()
                else: t["date"] = max(hoy, (max(d, hoy - dt.timedelta(days=1)) if days > 0 else d) + dt.timedelta(days=days)).isoformat()
        a.save(); self._ui.push()

    def task_delete(self, tid):
        a = self._ui.agent
        a.d["tasks"] = [t for t in a.d["tasks"] if t["id"] != tid]; a.save(); self._ui.push()

    def habit_log(self, name):
        self._ui.show(self._ui.agent.do_habit_log({"name": name, "amount": 1}), speak=False); self._ui.agent.save()

    def habit_add(self, name, goal):
        self._ui.agent.do_habit_add({"name": name, "goal": goal}); self._ui.agent.save(); self._ui.push()

    def memory_delete(self, i):
        a = self._ui.agent
        if 0 <= i < len(a.d["memory"]): a.d["memory"].pop(i); a.save(); self._ui.push()

    def focus_start(self, minutes): self._ui.show(self._ui.agent.do_focus({"minutes": minutes}), speak=False)
    def focus_stop(self): self._ui.show(self._ui.agent.do_stop_focus({}), speak=False)

    def focus_add(self, minutes):
        a = self._ui.agent
        if a.focus_end: a.focus_end += minutes * 60; a.focus_total += minutes * 60
        self._ui.push()

    def mail_refresh(self):
        ui = self._ui
        def work():
            try:
                if ui.agent.gmail.accounts():
                    ui.mails = ui.agent.gmail.unread(n=8); ui.agent.mails = ui.mails; ui.unread = len(ui.mails)
            except Exception as e:
                ui.call("status", f"Correo: {e}"[:46], "alert", 6)
            ui.push()
        threading.Thread(target=work, daemon=True).start()

    def mail_reply(self, i):
        ui = self._ui
        ui.agent.mails = ui.mails
        ui.background(lambda: ui.agent.do_mail_reply({"index": i + 1}), speak=False)

    def cfg(self, key, value):
        from mate_servicios import CONFIG
        ui = self._ui
        ui.cfg[key] = value
        try: data = json.loads(CONFIG.read_text(encoding="utf-8"))
        except Exception: data = {}
        data[key] = value
        CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if key == "voice_id": ui.speaker.set_voice(value)
        if key == "voice_speed": ui.speaker.speed = float(value)
        if key == "claps": ui.listener.claps_on = bool(value)
        if key == "clap_threshold": ui.listener.thr = float(value)
        if key == "argentinidad":
            import mate_argento; mate_argento.NIVEL = value
        ui.push()

    def voice_sample(self, vid):
        self._ui.speaker.sample(vid)

    def google_connect(self):
        """Un click: abre la pantalla de Google, elegís la cuenta y 'Permitir'."""
        ui = self._ui
        if getattr(ui, "connecting", False): return
        def work():
            ui.connecting = True; ui.push()
            ui.notch("Abrí la pestaña de Google y elegí tu cuenta", "", 30)
            try:
                email = ui.agent.gmail.connect()
                ui.show(f"✓ Conecté {email}: ya veo tu Gmail y tu Calendar", speak=False)
            except Exception as e:
                msg = str(e)
                if "org_internal" in msg or "access_denied" in msg:
                    msg = "Esa cuenta todavía no está habilitada. Pedile a quien te pasó Mate que la sume como usuario de prueba"
                elif "timed out" in msg.lower() or "timeout" in msg.lower():
                    msg = "Se venció el tiempo para autorizar. Probá de nuevo"
                ui.show(f"✗ No pude conectar la cuenta: {msg[:120]}", speak=False)
            finally:
                ui.connecting = False; ui.push()
        threading.Thread(target=work, daemon=True).start()

    def undo_cancel(self):
        if self._ui.undo_ev: self._ui.undo_ev.set()

    def task_delete_undo(self, tid):
        ui = self._ui
        def work():
            t = next((x for x in ui.agent.d["tasks"] if x["id"] == tid), None)
            if not t: return
            ui.agent.d["tasks"].remove(t); ui.agent.save(); ui.push()
            if not ui.undo_window(f"Borré “{t['text'][:24]}”", 5):
                ui.agent.d["tasks"].append(t); ui.agent.save(); ui.push()
        threading.Thread(target=work, daemon=True).start()

    def google_disconnect(self, alias):
        self._ui.agent.gmail.disconnect(alias); self._ui.push()

    def spotify_connect(self):
        """Un click: abre la pantalla de Spotify y 'Aceptar'. Necesita Spotify Premium."""
        ui = self._ui
        def work():
            from mate_servicios import Spotify
            ui.notch("Aceptá en la pestaña de Spotify", "", 30)
            try:
                who = Spotify.connect()
                ui.agent.spotify = Spotify()
                ui.show(f"✓ Conecté el Spotify de {who}: ahora pongo música sin abrir la app", speak=False)
            except Exception as e:
                msg = str(e)
                if "403" in msg or "not registered" in msg.lower() or "premium" in msg.lower():
                    msg = "Spotify no habilitó tu cuenta: hace falta Premium y que quien te pasó Mate te sume en su app"
                Spotify.disconnect(); ui.agent.spotify = Spotify()
                ui.show(f"✗ No pude conectar Spotify. {msg[:140]}", speak=False)
            ui.push()
        threading.Thread(target=work, daemon=True).start()

    def open_url(self, url):
        import webbrowser
        if url.startswith("https://"): webbrowser.open(url)

    def granola_set(self, key):
        """Clave de la API de Granola: Mate lee las notas sin abrir la ventana. Vacía = usa la ventana."""
        key = (key or "").strip()
        if not key:
            self.cfg("granola_key", ""); return {"ok": True, "msg": "Leo Granola desde su ventana"}
        import mate_granola as GA
        ok, err = GA.check(key)
        if ok: self.cfg("granola_key", key)
        return {"ok": ok, "msg": "✓ Granola conectado: leo tus notas sin abrir la ventana" if ok else f"✗ {err}"}

    def gemini_set(self, key):
        """Guarda la clave de Gemini si funciona. Vacía = vuelve a la voz 100% local."""
        key = (key or "").strip()
        if not key:
            self.cfg("gemini_key", ""); return {"ok": True, "msg": "Voz local (sin Gemini)"}
        import mate_gemini as G
        ok, err, models = G.check_key(key)         # el mejor modelo primero, después los de respaldo
        if not ok: return {"ok": False, "msg": f"✗ {err}"}
        self.cfg("gemini_model", models); self.cfg("gemini_key", key)
        nota = " (ahora Google está saturado: si tarda, uso los de respaldo o la voz local)" if err == "saturado" else ""
        return {"ok": True, "msg": f"✓ Gemini conectado ({models[0]}){nota}"}

    def spotify_disconnect(self):
        from mate_servicios import Spotify
        Spotify.disconnect(); self._ui.agent.spotify = Spotify(); self._ui.push()

    def onboard(self, name, ciudad="", coords=""):
        """Primer arranque: cómo se llama el usuario y su ciudad (para el clima)."""
        self.city_set(ciudad, coords)
        self.cfg("user_name", name.strip())

    def city_search(self, q):
        import mate_servicios as sv
        try: return sv.city_search(q)
        except Exception: return []

    def city_detect(self):
        import mate_servicios as sv
        try: return sv.city_detect()
        except Exception: return ""

    def city_set(self, label, coords=""):
        """Guarda la ciudad elegida (vacía = automática según tu conexión) y actualiza el clima."""
        self.cfg("ciudad", (label or "").strip()); self.cfg("coords", coords or "")
        ui = self._ui
        threading.Thread(target=getattr(ui, "_load_extras", lambda: None), daemon=True).start()

    def cfg_look(self, key, value):
        look = dict(self._ui.cfg.get("look") or {}); look[key] = value
        self.cfg("look", look)

    def cfg_desktop(self, key, value):
        d = dict(self._ui.cfg.get("desktop") or {}); d[key] = value
        self.cfg("desktop", d)

    def media(self, what):
        m = getattr(self._ui, "media", None)
        if m: m.cmd(what); time.sleep(0.4); self._ui.media_now = m.now(); self._ui.push()

    def file_action(self, action, paths, extra=""):
        threading.Thread(target=self._ui.file_action, args=(action, paths, extra), daemon=True).start()

    def drop_close(self): self._ui.drop_close()
    def drop_enter(self): self._ui.drop_enter()

    def quit(self): self._ui.window.destroy()
