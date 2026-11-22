"""Cosas del sistema que Mate muestra en la isla (ideas tomadas de Nusa):
- historial del portapapeles (sólo en memoria: nunca se guarda en disco)
- estado de un vistazo: batería, Wi-Fi, auriculares
- avisos de volumen
- si hay una app en pantalla completa (para correrse)
Windows con ctypes; en Mac lo que se puede con AppleScript/pmset."""
import ctypes, re, subprocess, sys, time

IS_WIN = sys.platform == "win32"


# ---------------- portapapeles ----------------
def _clip_seq():
    return ctypes.windll.user32.GetClipboardSequenceNumber() if IS_WIN else 0


def clip_text():
    """Texto actual del portapapeles (rápido, sin PowerShell)."""
    if not IS_WIN:
        try: return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2).stdout
        except Exception: return ""
    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    u.GetClipboardData.restype = ctypes.c_void_p; k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [ctypes.c_void_p]; k.GlobalUnlock.argtypes = [ctypes.c_void_p]
    for _ in range(3):
        if u.OpenClipboard(None): break
        time.sleep(0.05)
    else:
        return ""
    try:
        h = u.GetClipboardData(13)          # CF_UNICODETEXT
        if not h: return ""
        p = k.GlobalLock(h)
        try: return ctypes.wstring_at(p) if p else ""
        finally: k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


SECRETO = re.compile(r"^(?=.*\d)(?=.*[A-Za-z])\S{12,}$|^(sk|pk|ghp|gho|xox[abp]|AIza|grn)_?[\w-]{10,}|-----BEGIN")


class ClipHistory:
    """Últimos textos copiados. No guarda lo que parece contraseña o clave."""
    def __init__(self, n=25):
        self.items, self.n, self.seq = [], n, None

    def poll(self):
        s = _clip_seq() if IS_WIN else None
        if IS_WIN and s == self.seq: return False
        self.seq = s
        t = (clip_text() or "").strip()
        if not t or len(t) > 5000 or SECRETO.search(t): return False
        if self.items and self.items[0]["text"] == t: return False
        self.items = [i for i in self.items if i["text"] != t]
        self.items.insert(0, {"text": t, "ts": time.time()})
        del self.items[self.n:]
        return True


# ---------------- estado del sistema ----------------
class _PowerStatus(ctypes.Structure):
    _fields_ = [("ACLineStatus", ctypes.c_byte), ("BatteryFlag", ctypes.c_byte), ("BatteryLifePercent", ctypes.c_byte),
                ("SystemStatusFlag", ctypes.c_byte), ("BatteryLifeTime", ctypes.c_ulong), ("BatteryFullLifeTime", ctypes.c_ulong)]


def battery():
    """{'pct': 0-100, 'plugged': bool} o None si no hay batería."""
    if IS_WIN:
        s = _PowerStatus()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)) or s.BatteryFlag == -128 or s.BatteryLifePercent == -1:
            return None
        return {"pct": s.BatteryLifePercent & 0xFF, "plugged": s.ACLineStatus == 1}
    try:
        out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=3).stdout
        m = re.search(r"(\d+)%", out)
        return {"pct": int(m.group(1)), "plugged": "AC Power" in out} if m else None
    except Exception:
        return None


def online():
    """¿Hay internet? (Windows: estado de red del sistema, sin hacer pedidos)."""
    if IS_WIN:
        flags = ctypes.c_ulong()
        return bool(ctypes.windll.wininet.InternetGetConnectedState(ctypes.byref(flags), 0))
    return True


def audio_device():
    """Nombre del dispositivo de salida de audio por defecto (para avisar 'auriculares conectados')."""
    if not IS_WIN: return None
    try:
        from pycaw.pycaw import AudioUtilities
        return getattr(AudioUtilities.GetSpeakers(), "FriendlyName", None)
    except Exception:
        return None


def volume_pct():
    """Volumen maestro 0-100 y si está silenciado."""
    if not IS_WIN:
        try:
            v = subprocess.run(["osascript", "-e", "output volume of (get volume settings)"], capture_output=True,
                               text=True, timeout=2).stdout.strip()
            return {"pct": int(v), "mute": False} if v.isdigit() else None
        except Exception:
            return None
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        sp = AudioUtilities.GetSpeakers()
        ep = getattr(sp, "EndpointVolume", None)
        if ep is None:
            ep = sp.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)
        return {"pct": round(ep.GetMasterVolumeLevelScalar() * 100), "mute": bool(ep.GetMute())}
    except Exception:
        return None


def fullscreen_app(own_hwnd=None):
    """¿La ventana de adelante ocupa toda la pantalla (video, presentación, juego)? -> Mate se corre."""
    if not IS_WIN: return False
    u = ctypes.windll.user32
    h = u.GetForegroundWindow()
    if not h or h == own_hwnd: return False
    cls = ctypes.create_unicode_buffer(64); u.GetClassNameW(h, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"): return False   # escritorio / barra de tareas
    from ctypes import wintypes
    r = wintypes.RECT(); u.GetWindowRect(h, ctypes.byref(r))
    mon = u.MonitorFromWindow(h, 2)
    class MI(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT), ("dwFlags", ctypes.c_ulong)]
    mi = MI(); mi.cbSize = ctypes.sizeof(MI)
    if not u.GetMonitorInfoW(mon, ctypes.byref(mi)): return False
    m = mi.rcMonitor
    return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom
