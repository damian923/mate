"""Servicios de Mate: Gmail (varias cuentas), Spotify, apps instaladas, web y control del sistema."""
import base64, ctypes, difflib, html, json, os, re, subprocess, urllib.parse, urllib.request, webbrowser
from email.mime.text import MIMEText
from pathlib import Path

DATA_DIR = Path.home() / ".mate"
CONFIG = DATA_DIR / "config.json"
APP_DIR = Path(__file__).resolve().parent


def config():
    try: return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception: return {}


# ============ Gmail ============
GMAIL_DIR = DATA_DIR / "gmail"
SCOPES = ["https://www.googleapis.com/auth/gmail.modify",      # leer, borradores y enviar
          "https://www.googleapis.com/auth/calendar.events"]   # ver y crear eventos


class Gmail:
    def accounts(self):
        if not GMAIL_DIR.exists(): return []
        out, seen, known = [], set(), self.emails()
        for a in sorted(p.stem for p in GMAIL_DIR.glob("*.json") if not p.stem.startswith("_")):
            e = known.get(a)
            if e and e in seen:          # la misma cuenta guardada dos veces: me quedo con una
                (GMAIL_DIR / f"{a}.json").unlink(missing_ok=True); known.pop(a, None)
                (DATA_DIR / "cuentas.json").write_text(json.dumps(known, indent=1), encoding="utf-8")
                continue
            if e: seen.add(e)
            out.append(a)
        return out

    def label(self, alias):
        """El mail de una cuenta conectada (lo pregunta a Google la primera vez y lo recuerda)."""
        m = self.emails()
        if alias not in m:
            try:
                self._save_email(alias, self._svc(alias).users().getProfile(userId="me").execute()["emailAddress"])
                m = self.emails()
            except Exception:
                return alias
        return m.get(alias, alias)

    def connect(self, alias=None):
        """Abre la pantalla de Google para autorizar una cuenta y la guarda.
        Sin alias, lo arma solo a partir del mail (ej. vos@empresa.com -> 'empresa')."""
        from google_auth_oauthlib.flow import InstalledAppFlow
        secret = DATA_DIR / "google_credentials.json"           # una propia, si el usuario la puso
        if not secret.exists(): secret = APP_DIR / "google_credentials.json"   # la que viene con Mate
        if not secret.exists():
            raise RuntimeError("Falta la credencial de Google de Mate (google_credentials.json)")
        flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
        creds = flow.run_local_server(port=0, prompt="select_account consent", timeout_seconds=300,
                                      success_message="¡Listo! Mate ya está conectado. Podés cerrar esta pestaña 🧉")
        GMAIL_DIR.mkdir(parents=True, exist_ok=True)
        tmp = GMAIL_DIR / "_nueva.json"
        tmp.write_text(creds.to_json())
        email = self._svc("_nueva").users().getProfile(userId="me").execute()["emailAddress"]
        if not alias:
            user, dom = email.lower().split("@")
            alias = "personal" if dom in ("gmail.com", "googlemail.com") else dom.split(".")[0]
            if alias == "personal" and (GMAIL_DIR / "personal.json").exists() and self.emails().get("personal") != email:
                alias = user
        alias = re.sub(r"[^\w-]", "", alias.lower())
        for a_, e_ in self.emails().items():       # la misma cuenta conectada antes con otro nombre
            if e_ == email and a_ != alias: (GMAIL_DIR / f"{a_}.json").unlink(missing_ok=True)
        tmp.replace(GMAIL_DIR / f"{alias}.json")
        self._save_email(alias, email)
        return email

    def emails(self):
        try: return json.loads((DATA_DIR / "cuentas.json").read_text(encoding="utf-8"))
        except Exception: return {}

    def _save_email(self, alias, email):
        m = self.emails(); m[alias] = email
        (DATA_DIR / "cuentas.json").write_text(json.dumps(m, indent=1), encoding="utf-8")

    def disconnect(self, alias):
        (GMAIL_DIR / f"{alias}.json").unlink(missing_ok=True)
        m = self.emails(); m.pop(alias, None)
        (DATA_DIR / "cuentas.json").write_text(json.dumps(m, indent=1), encoding="utf-8")

    def _svc(self, alias, api="gmail", version="v1"):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        f = GMAIL_DIR / f"{alias}.json"
        creds = Credentials.from_authorized_user_file(str(f), SCOPES)
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request()); f.write_text(creds.to_json())
        return build(api, version, credentials=creds, cache_discovery=False)

    # ---------- Google Calendar (mismas cuentas) ----------
    def events(self, day, alias=None):
        """Eventos de un día (datetime.date) en todas las cuentas o una."""
        import datetime as dt
        tz = dt.datetime.now().astimezone().tzinfo
        start = dt.datetime.combine(day, dt.time.min, tz).isoformat()
        end = dt.datetime.combine(day, dt.time.max, tz).isoformat()
        out = []
        for acc in self._pick(alias):
            try:
                items = self._svc(acc, "calendar", "v3").events().list(
                    calendarId="primary", timeMin=start, timeMax=end, singleEvents=True,
                    orderBy="startTime").execute().get("items", [])
            except Exception:
                continue  # cuenta conectada antes de sumar Calendar: reconectarla
            for e in items:
                st = e["start"].get("dateTime", "")
                out.append({"account": acc, "time": st[11:16] if st else "todo el día",
                            "title": e.get("summary", "(sin título)")})
        return sorted(out, key=lambda e: e["time"])

    def events_range(self, d0, d1, alias=None):
        """Eventos entre dos fechas (inclusive) de todas las cuentas: [{date, time, end, title, account}]."""
        import datetime as dt
        tz = dt.datetime.now().astimezone().tzinfo
        start = dt.datetime.combine(d0, dt.time.min, tz).isoformat()
        end = dt.datetime.combine(d1, dt.time.max, tz).isoformat()
        out = []
        for acc in self._pick(alias):
            try:
                items = self._svc(acc, "calendar", "v3").events().list(
                    calendarId="primary", timeMin=start, timeMax=end, singleEvents=True,
                    orderBy="startTime", maxResults=250).execute().get("items", [])
            except Exception:
                continue
            for e in items:
                if e.get("status") == "cancelled": continue
                st, en = e["start"], e.get("end", {})
                if "dateTime" in st:
                    out.append({"date": st["dateTime"][:10], "time": st["dateTime"][11:16],
                                "end": en.get("dateTime", "")[11:16], "title": e.get("summary", "(sin título)"),
                                "account": acc, "link": e.get("hangoutLink", "")})
                else:
                    out.append({"date": st.get("date", ""), "time": "todo el día", "end": "",
                                "title": e.get("summary", "(sin título)"), "account": acc, "link": ""})
        return sorted(out, key=lambda e: (e["date"], e["time"]))

    def add_event(self, title, day, hour=None, minutes=60, alias=None):
        import datetime as dt
        acc = self._pick(alias or config().get("calendar_account"))[0]
        if hour:
            h, m = map(int, str(hour).split(":")[:2])
            tz = dt.datetime.now().astimezone().tzinfo
            s = dt.datetime.combine(day, dt.time(h, m), tz)
            body = {"summary": title, "start": {"dateTime": s.isoformat()},
                    "end": {"dateTime": (s + dt.timedelta(minutes=int(minutes or 60))).isoformat()}}
        else:
            body = {"summary": title, "start": {"date": day.isoformat()},
                    "end": {"date": (day + dt.timedelta(days=1)).isoformat()}}
        self._svc(acc, "calendar", "v3").events().insert(calendarId="primary", body=body).execute()
        return acc

    def _pick(self, alias):
        accs = self.accounts()
        if not accs: raise RuntimeError("No hay cuentas de Gmail conectadas. Decí 'conectar gmail <nombre>'")
        if not alias: return accs
        m = difflib.get_close_matches(alias.lower(), accs, 1, 0.4)
        if not m: raise RuntimeError(f"No conozco la cuenta '{alias}'. Tengo: {', '.join(accs)}")
        return m

    def unread(self, alias=None, n=5):
        """Devuelve [(alias, id, de, asunto, resumen)] de todas o una cuenta."""
        out = []
        for acc in self._pick(alias):
            s = self._svc(acc)
            ids = s.users().messages().list(userId="me", q="is:unread in:inbox category:primary", maxResults=n).execute()
            for m in ids.get("messages", []):
                msg = s.users().messages().get(userId="me", id=m["id"], format="metadata",
                                               metadataHeaders=["From", "Subject"]).execute()
                h = {x["name"]: x["value"] for x in msg["payload"]["headers"]}
                out.append({"account": acc, "id": m["id"], "thread": msg["threadId"],
                            "from": h.get("From", ""), "subject": h.get("Subject", "(sin asunto)"),
                            "snippet": html.unescape(msg.get("snippet", ""))})
        return out

    def send(self, mail, body, draft_only=False):
        s = self._svc(mail["account"])
        to = re.search(r"<(.+?)>", mail["from"]); to = to.group(1) if to else mail["from"]
        msg = MIMEText(body, "plain", "utf-8")
        msg["To"], msg["Subject"] = to, "Re: " + re.sub(r"^(re:\s*)+", "", mail["subject"], flags=re.I)
        raw = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode(), "threadId": mail["thread"]}
        if draft_only:
            s.users().drafts().create(userId="me", body={"message": raw}).execute()
        else:
            s.users().messages().send(userId="me", body=raw).execute()
        s.users().messages().modify(userId="me", id=mail["id"], body={"removeLabelIds": ["UNREAD"]}).execute()
        return to


# ============ Spotify ============
SPOTIFY_CLIENT_ID = "5f124d9bb651437593fa9e38322704db"   # app "Mate" en developer.spotify.com (no es secreto)
VK = {"playpause": 0xB3, "next": 0xB0, "prev": 0xB1, "vol_up": 0xAF, "vol_down": 0xAE, "mute": 0xAD}


def media_key(name, times=1):
    for _ in range(times):
        ctypes.windll.user32.keybd_event(VK[name], 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK[name], 0, 2, 0)


class Spotify:
    """Con API (necesita Premium + app en developer.spotify.com) reproduce lo que pidas.
    Sin API: abre la búsqueda en Spotify y controla play/pausa/siguiente con teclas multimedia."""
    TOKEN = DATA_DIR / "spotify_token"

    def __init__(self, connect=False):
        """Usa la API sólo si el usuario ya conectó su cuenta (o connect=True para hacerlo ahora).
        Con la app 'Mate' compartida se usa PKCE: no hace falta clave secreta."""
        self.sp = None
        c = config().get("spotify", {})
        if not (connect or self.TOKEN.exists()): return
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyOAuth, SpotifyPKCE
            kw = dict(client_id=c.get("client_id") or SPOTIFY_CLIENT_ID, redirect_uri="http://127.0.0.1:8888/callback",
                      open_browser=True, scope="user-modify-playback-state user-read-playback-state",
                      cache_path=str(self.TOKEN))
            auth = SpotifyOAuth(client_secret=c["client_secret"], **kw) if c.get("client_secret") else SpotifyPKCE(**kw)
            self.sp = spotipy.Spotify(auth_manager=auth)
        except Exception:
            self.sp = None

    @classmethod
    def connect(cls):
        """Abre la pantalla de Spotify para autorizar a Mate. Devuelve el nombre de la cuenta."""
        s = cls(connect=True)
        if not s.sp: raise RuntimeError("No pude iniciar la conexión con Spotify")
        return s.sp.current_user().get("display_name") or "tu cuenta"

    @classmethod
    def disconnect(cls):
        cls.TOKEN.unlink(missing_ok=True)

    def play(self, query, kind="track"):
        if not self.sp:
            os.startfile("spotify:search:" + urllib.parse.quote(query))
            return f"Abrí Spotify buscando '{query}'. Para que lo reproduzca solo, configurá la API (ver GUIA.md)"
        kind = kind if kind in ("track", "artist", "album", "playlist") else "track"
        res = self.sp.search(q=query, type=kind, limit=1)[kind + "s"]["items"]
        if not res: return f"No encontré '{query}' en Spotify"
        item = res[0]
        devices = self.sp.devices()["devices"]
        if not devices:   # Spotify cerrado: lo abre y espera a que aparezca como dispositivo
            import time, subprocess
            subprocess.Popen(["cmd", "/c", "start", "/min", "", "spotify:"], creationflags=0x08000000)
            for _ in range(15):
                time.sleep(1)
                devices = self.sp.devices()["devices"]
                if devices: break
            if not devices: raise RuntimeError("sin dispositivo de Spotify")
        dev = next((d["id"] for d in devices if d["is_active"]), devices[0]["id"] if devices else None)
        if kind == "track": self.sp.start_playback(device_id=dev, uris=[item["uri"]])
        else: self.sp.start_playback(device_id=dev, context_uri=item["uri"])
        who = item.get("artists", [{}])[0].get("name", "") if kind in ("track", "album") else ""
        return f"♫ {item['name']}" + (f" – {who}" if who else "")

    def control(self, what):
        if self.sp:
            try:
                {"pause": self.sp.pause_playback, "resume": self.sp.start_playback,
                 "next": self.sp.next_track, "prev": self.sp.previous_track}[what]()
                return "✓"
            except Exception:
                pass
        media_key({"pause": "playpause", "resume": "playpause", "next": "next", "prev": "prev"}[what])
        return "✓"


# ============ Apps instaladas ============
class Apps:
    ALIASES = {"navegador": "edge", "bloc de notas": "notepad", "calculadora": "calculator",
               "explorador": "file explorer", "configuración": "settings", "terminal": "terminal"}

    def __init__(self):
        self.index = {}
        self.refresh()

    def refresh(self):
        idx = {}
        for base in [Path(os.environ.get("ProgramData", "")) / "Microsoft/Windows/Start Menu/Programs",
                     Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
                     Path.home() / "Desktop"]:
            if base.exists():
                for f in base.rglob("*.lnk"):
                    if "uninstall" not in f.stem.lower() and "desinstalar" not in f.stem.lower():
                        idx[f.stem.lower()] = ("lnk", str(f))
        try:  # apps de la Microsoft Store (WhatsApp, Spotify, Calculadora, etc.)
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "Get-StartApps | ConvertTo-Json -Compress"],
                                 capture_output=True, text=True, timeout=20,
                                 creationflags=0x08000000).stdout
            for a in json.loads(out or "[]"):
                idx.setdefault(a["Name"].lower(), ("appid", a["AppID"]))
        except Exception:
            pass
        self.index = idx

    def open(self, name):
        n = self.ALIASES.get(name.lower().strip(), name.lower().strip())
        keys = list(self.index)
        hit = next((k for k in keys if k == n), None) \
            or next((k for k in keys if k.startswith(n)), None) \
            or next((k for k in keys if n in k), None) \
            or (difflib.get_close_matches(n, keys, 1, 0.55) or [None])[0]
        if not hit:
            raise RuntimeError(f"No encontré '{name}' instalado")
        kind, target = self.index[hit]
        if kind == "lnk": os.startfile(target)
        else: subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{target}"])
        return hit


# ============ Web ============
def web_open(query):
    if re.match(r"^[\w-]+(\.[\w-]+)+(/.*)?$", query.strip()):
        webbrowser.open("https://" + query.strip())
    else:
        webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(query))


def _get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "Mate-asistente/1.0"})
    return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")


def city_search(q, n=6):
    """Sugerencias de ciudades mientras escribís (Open-Meteo, gratis y sin clave)."""
    if len((q or "").strip()) < 2: return []
    url = ("https://geocoding-api.open-meteo.com/v1/search?count=%d&language=es&name=" % n) + urllib.parse.quote(q.strip())
    res = json.loads(_get(url)).get("results") or []
    out = []
    for r in res:
        label = ", ".join(x for x in (r.get("name"), r.get("admin1"), r.get("country")) if x)
        out.append({"label": label, "coords": f"{r['latitude']:.3f},{r['longitude']:.3f}"})
    return out


def city_detect():
    """Ciudad aproximada según tu conexión a internet (la misma que usa el clima si no elegiste una)."""
    data = json.loads(_get("https://wttr.in/?format=j1&lang=es"))
    a = data["nearest_area"][0]
    return ", ".join(x[0]["value"] for x in (a["areaName"], a["region"], a["country"]) if x and x[0]["value"])


def weather(place=""):
    """Pronóstico de wttr.in (gratis, sin clave)."""
    coords = bool(re.fullmatch(r"-?\d+(\.\d+)?,-?\d+(\.\d+)?", place or ""))
    q = place if coords else (urllib.parse.quote("~" + place) if place else "")   # vacío = según tu conexión
    data = json.loads(_get("https://wttr.in/" + q + "?format=j1&lang=es"))
    area = data["nearest_area"][0]["areaName"][0]["value"]
    out = [f"Lugar: {area}"]
    cur = data["current_condition"][0]
    out.append(f"Ahora: {cur['temp_C']}°C, {cur.get('lang_es', cur['weatherDesc'])[0]['value']}")
    for d, label in zip(data["weather"][:3], ["Hoy", "Mañana", "Pasado"]):
        desc = d["hourly"][4].get("lang_es", d["hourly"][4]["weatherDesc"])[0]["value"]
        rain = max(int(h.get("chanceofrain", 0)) for h in d["hourly"])
        out.append(f"{label}: mín {d['mintempC']}°C, máx {d['maxtempC']}°C, {desc}, lluvia {rain}%")
    return out


def web_snippets(query, n=6):
    """Noticias (Google News RSS) + Wikipedia. Sin claves ni scraping de buscadores."""
    out = []
    try:
        rss = _get("https://news.google.com/rss/search?q=" + urllib.parse.quote(query) + "&hl=es-419&gl=AR&ceid=AR:es-419")
        for t, d in re.findall(r"<item><title>(.*?)</title>.*?<pubDate>(.*?)</pubDate>", rss, re.S)[:n]:
            out.append(f"Noticia ({d[5:16]}): {html.unescape(t)}")
    except Exception:
        pass
    try:
        s = json.loads(_get("https://es.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=2&srsearch="
                            + urllib.parse.quote(query)))
        for r in s["query"]["search"]:
            out.append(f"Wikipedia – {r['title']}: " + html.unescape(re.sub(r"<.*?>", "", r["snippet"])))
    except Exception:
        pass
    return out


# ============ Sistema ============
def volume(direction, steps=5):
    media_key({"up": "vol_up", "down": "vol_down", "mute": "mute"}[direction], 1 if direction == "mute" else steps)


def lock_pc():
    ctypes.windll.user32.LockWorkStation()


def clipboard_text():
    return subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                          capture_output=True, text=True, timeout=10, creationflags=0x08000000).stdout.strip()

