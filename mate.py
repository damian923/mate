"""
Mate - asistente de escritorio 100% local (Windows).
IA: Ollama (llama3.2:3b por defecto). Voz: Vosk + pyttsx3 (offline). Doble aplauso para activar.
"""
import difflib, json, os, shutil, threading, time, uuid, datetime as dt
from pathlib import Path
import urllib.request

import mate_servicios as sv
import mate_argento as ar
from mate_audio import Listener, Speaker, log

CFG = sv.config()
MODEL = os.environ.get("MATE_MODEL", CFG.get("model", "llama3.2:3b"))
MODEL_LONG = CFG.get("model_long", "hf.co/unsloth/Qwen3.5-2B-GGUF:Q4_K_M")   # reuniones y textos largos
WAKE = CFG.get("wake_word", "mate")
NAME = CFG.get("name", "Mate")
ar.NIVEL = CFG.get("argentinidad", "normal")
OLLAMA = "http://localhost:11434/api/chat"
HOME = Path.home()
DATA_DIR = HOME / ".mate"
DATA_FILE = DATA_DIR / "data.json"
DESKTOP = next((p for p in [HOME / "Desktop", HOME / "OneDrive" / "Escritorio", HOME / "Escritorio",
                            HOME / "OneDrive" / "Desktop"] if p.exists()), HOME / "Desktop")
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
STATUSES = ["pendiente", "en curso", "esperando", "hecha"]
STATUS_ICON = {"pendiente": "○", "en curso": "◐", "esperando": "⏸", "hecha": "✔"}
STATUS_COLOR = {"pendiente": "#e0e0e0", "en curso": "#4fc3f7", "esperando": "#fbbf24", "hecha": "#66bb6a"}


def load():
    try: d = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception: d = {}
    for k, v in {"tasks": [], "reminders": [], "habits": {}, "memory": [], "last_cleanup": None}.items():
        d.setdefault(k, v)
    for t in d["tasks"]:  # migra tareas viejas
        t.setdefault("id", uuid.uuid4().hex[:8]); t.setdefault("date", None)
        t.setdefault("status", "hecha" if t.pop("done", False) else "pendiente")
    return d


def save(d):
    DATA_DIR.mkdir(exist_ok=True)
    DATA_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def parse_date(s):
    if not s: return None
    try: return dt.date.fromisoformat(str(s)[:10])
    except ValueError: return None


def nice_date(iso):
    d = parse_date(iso)
    if not d: return "sin fecha"
    delta = (d - dt.date.today()).days
    return {0: "hoy", 1: "mañana", -1: "ayer"}.get(delta, f"{DIAS[d.weekday()]} {d.day}/{d.month}")


# ---------------- LLM ----------------
SYSTEM = """Sos {name}, un asistente de escritorio argentino que ama el mate. {persona}
Respondé SOLO con JSON corto: {"action": "...", "args": {...}}. Solo en chat agregá "reply" con tu respuesta (1 o 2 frases).
Acciones:
- add_task {"text","date"}       (date YYYY-MM-DD o vacío; "el jueves" = el próximo jueves de la tabla)
- task_status {"task","status"}  (task = número o texto; status: pendiente|en curso|esperando|hecha)
- move_task {"task","date"}
- delete_task {"task"}
- list_tasks {"range"}           (today|week|all)
- week_board {}                  (mostrar la agenda semanal en pantalla)
- reminder {"text","minutes"} o {"text","at":"HH:MM"} | timer {"minutes","label"}
- focus {"minutes":25} | stop_focus {}
- habit_add {"name","goal"} | habit_log {"name","amount"} | habit_status {}
- remember {"fact"}              (el usuario te pide que te acuerdes de algo sobre él)
- forget {"about"} | memories {}
- cal_events {"date"}            (agenda de Google Calendar de ese día)
- cal_add {"title","date","time","minutes"}  (crear evento en Google Calendar)
- morning_brief {}               (qué tengo hoy / resumen del día)
- clean_desktop {} | undo_cleanup {}
- open_app {"name"}              (cualquier programa instalado)
- slack_send {"to","text","file"}  (mandar mensaje por Slack; text = lo que pidió decir, en primera persona y con sus palabras; file = descripción del archivo, opcional)
- find_file {"query"}            (buscar un archivo en la compu y abrirlo)
- record_meeting {}              (Granola: empezar a grabar/transcribir una reunión)
- stop_recording {}              (Granola: frenar la grabación; después te propone las tareas que te asignaron)
- meeting_note {"text"}         (anotar algo como tarea, durante una reunión queda con la etiqueta de la reunión)
- meeting_now {}                 (qué se decidió hasta ahora en la reunión que se está grabando)
- meeting_brief {"meeting"}      (machete: qué se habló la última vez en una reunión o con una persona)
- meeting_tasks {"meeting"}      (sacar de Granola las tareas que me asignaron en una reunión; meeting = parte del título, opcional)
- app_click {"app","button"}     (apretar un botón por su nombre dentro de una app)
- app_type {"app","text","keys"} (escribir texto o usar atajo en una app; keys: ^n = Ctrl+N, {ENTER})
- web_open {"query"} | web_answer {"question"}  (web_answer: clima, noticias, resultados, precios)
- music_play {"query","kind"} | music_control {"what": pause|resume|next|prev}
- volume {"direction": up|down|mute} | lock_pc {}
- dolar {}                       (cotización del dólar hoy: oficial, blue, MEP)
- mail_check {"account"} | mail_read {"index"} | mail_reply {"index","instructions"} | mail_connect {"alias"}
- clipboard {"instructions"}
- chat {}                        (charla; la respuesta va en reply)

Ejemplos:
"agendá pagar el monotributo el viernes" -> {"action":"add_task","args":{"text":"pagar el monotributo","date":"<fecha del viernes>"}}
"acordate que mi hermana se llama Sofi" -> {"action":"remember","args":{"fact":"su hermana se llama Sofi"}}
"ya llamé al banco" -> {"action":"task_status","args":{"task":"llamar al banco","status":"hecha"}}
"lo de la propuesta está esperando respuesta" -> {"action":"task_status","args":{"task":"propuesta","status":"esperando"}}
"qué tengo hoy" -> {"action":"morning_brief","args":{}}
"tomé dos vasos de agua" -> {"action":"habit_log","args":{"name":"agua","amount":2}}
"poné Charly García" -> {"action":"music_play","args":{"query":"Charly García","kind":"artist"}}
"cómo va a estar el clima mañana" -> {"action":"web_answer","args":{"question":"clima mañana en mi ciudad"}}
"mandale a Lara por slack el excel de ventas diciendo que ya está listo" -> {"action":"slack_send","args":{"to":"Lara","text":"Ya está listo","file":"excel ventas"}}
"abrí granola y empezá a grabar" -> {"action":"record_meeting","args":{}}
"contame un chiste" -> {"action":"chat","args":{},"reply":"¿Sabés por qué el mate nunca se pelea? Porque siempre está en ronda."}
"cómo estás" -> {"action":"chat","args":{},"reply":"¡Bien! Acá, cebándome uno. ¿Vos?"}
"¿cómo se llama mi hermana?" -> {"action":"chat","args":{},"reply":"Sofi, me lo contaste vos."}
Nunca uses otra clave que "text" para add_task. Si es una pregunta sobre algo que ya sabés del usuario, usá chat.

Hoy es {today}. Ahora son las {now}. Próximos días:
{days}
Cuentas de Google conectadas: {accounts}.
Lo que sabés del usuario:
{memory}"""


def llm(messages, as_json=True, temp=0.2):
    body = {"model": MODEL, "messages": messages, "stream": False, "keep_alive": "2h",
            "options": {"temperature": temp, "num_ctx": 4096, "num_predict": 90 if as_json else 160}}
    if as_json: body["format"] = "json"
    req = urllib.request.Request(OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["message"]["content"]


def ask_long(prompt, system="Respondé en español rioplatense, claro y concreto.", max_tokens=500, temp=0.2):
    """Para textos largos (reuniones, documentos): Qwen3.5 2B de Unsloth, 262K de contexto.
    Se carga solo cuando hace falta y se descarga a los 3 minutos para liberar memoria."""
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    need = int(len(prompt) / 3) + max_tokens + 512
    ctx = 16384 if need <= 16384 else 65536          # tamaños fijos: así no recarga el modelo cada vez
    body = {"model": MODEL_LONG, "messages": msgs, "stream": False, "keep_alive": "10m", "think": False,
            "options": {"temperature": temp, "num_ctx": ctx, "num_predict": max_tokens}}
    req = urllib.request.Request(OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            return json.loads(r.read())["message"]["content"]
    except Exception:
        return ask(prompt[-12000:], system)   # si no está el modelo largo, uso el normal con lo último


def warm_long_model(minutes=40):
    """Carga el modelo de reuniones en memoria antes de que haga falta (y lo deja listo un rato)."""
    body = {"model": MODEL_LONG, "messages": [{"role": "user", "content": "hola"}], "stream": False,
            "keep_alive": f"{minutes}m", "think": False, "options": {"num_predict": 1, "num_ctx": 16384}}
    try:
        urllib.request.urlopen(urllib.request.Request(OLLAMA, json.dumps(body).encode(),
                                                      {"Content-Type": "application/json"}), timeout=300).read()
    except Exception:
        pass


def ask(prompt, system="Respondé en español rioplatense, claro y breve."):
    return llm([{"role": "system", "content": system}, {"role": "user", "content": prompt}], as_json=False, temp=0.5)


EXT = {"Imágenes": "jpg jpeg png gif webp bmp svg heic", "Documentos": "pdf doc docx txt odt rtf md",
       "Planillas": "xls xlsx csv ods", "Presentaciones": "ppt pptx key odp",
       "Videos": "mp4 mov avi mkv webm", "Audio": "mp3 wav m4a flac ogg",
       "Comprimidos": "zip rar 7z tar gz", "Instaladores": "exe msi"}
EXT = {e: folder for folder, exts in EXT.items() for e in exts.split()}


class Agent:
    def __init__(self, ui):
        self.ui, self.d, self.history = ui, load(), []
        self.focus_end, self.focus_break, self.focus_total = None, False, 1500
        self.gmail, self.spotify, self.mails = sv.Gmail(), sv.Spotify(), []
        self.apps, self.last_list, self.last_mail_check = None, [], time.time()
        threading.Thread(target=lambda: setattr(self, "apps", sv.Apps()), daemon=True).start()
        threading.Thread(target=self._warm_up, daemon=True).start()

    def save(self): save(self.d)

    @staticmethod
    def _warm_up():
        """Carga el modelo en memoria al arrancar para que el primer pedido sea rápido."""
        for _ in range(10):
            try: llm([{"role": "user", "content": "hola"}], as_json=False); return
            except Exception: time.sleep(6)   # Ollama todavía arrancando con Windows

    def system_prompt(self):
        today = dt.date.today()
        days = "\n".join(f"{DIAS[(today + dt.timedelta(i)).weekday()]} = {(today + dt.timedelta(i)).isoformat()}"
                         for i in range(8))
        mem = "\n".join(f"- {m}" for m in self.d["memory"]) or "(nada todavía)"
        return (SYSTEM.replace("{name}", NAME).replace("{persona}", ar.persona()).replace("{today}", f"{DIAS[today.weekday()]} {today.isoformat()}")
                .replace("{now}", dt.datetime.now().strftime("%H:%M")).replace("{days}", days)
                .replace("{accounts}", ", ".join(self.gmail.accounts()) or "ninguna").replace("{memory}", mem))

    # Atajos fijos: los pedidos más comunes no pasan por la IA (instantáneo y sin errores)
    FAST = [
        (r"^(?:che\s+)?(?:acordate|acuérdate|acordá|recordá)\s+(?:de\s+)?(?:que\s+)?(.+)", "remember", "fact"),
        (r"^(?:che\s+)?(?:anot[aá]|anotame|tom[aá] nota(?: de)?)\s+(?!la tarea)(?:que\s+|esto:?\s+)?(.+)", "meeting_note", "text"),
        (r"^(?:qu[eé] (?:se )?(?:decidi[oó]|decidimos|qued[oó])|resum[ií](?:me)? la reuni[oó]n|c[oó]mo viene la reuni[oó]n)(?: hasta ahora)?$", "meeting_now", None),
        (r"^qu[eé] (?:hablamos|vimos|charlamos|quedamos) (?:la [uú]ltima vez )?(?:con|en|de) (.+?)(?: la [uú]ltima vez)?$", "meeting_brief", "meeting"),
        (r"^(?:qu[eé] tengo (?:para )?hoy|resumen del d[ií]a|mi d[ií]a|c[oó]mo viene (?:el|mi) d[ií]a)\??$", "morning_brief", None),
        (r"^qu[eé] tengo (?:para )?(?:esta|la) semana\??$", "list_tasks", "range=week"),
        (r"^(?:mostrame|abr[ií]|ver) (?:la |mi )?(?:semana|agenda semanal|tablero)$", "week_board", None),
        (r"^qu[eé] sab[eé]s de m[ií]\??$", "memories", None),
        (r"^(?:ya\s+)?(?:termin[eé]|complet[eé]|hice|list[oa] con)\s+(?:lo de\s+|la tarea\s+|de\s+)?(.+)", "task_status", "task"),
        (r"^(?:abr[ií] granola y )?(?:empez[aá]|arranc[aá]|comenz[aá]) (?:a )?grab(?:ar|ación)(?: la reuni[oó]n)?$|^grab[aá] (?:la|esta) reuni[oó]n$", "record_meeting", None),
        (r"^(?:par[aá]|fren[aá]|cort[aá]|deten[eé]) (?:la )?grabaci[oó]n$|^dej[aá] de grabar$", "stop_recording", None),
        (r"^(?:qu[eé] )?tareas? (?:me (?:quedaron|tocaron|asignaron) )?de la (?:[uú]ltima )?reuni[oó]n(?: (?:con|de) (.+))?$", "meeting_tasks", "meeting"),
        (r"^(?:mate,?\s+)?(?:(?:pas[aá]|mov[eé])(?:me|mela|la)?\s+la tarea (?:de(?:l| la)?\s+)?(.+?)|la tarea (?:de(?:l| la)?\s+)?(.+?),?\s+(?:pas[aá]|mov[eé])(?:mela|la|me)?)\s+(?:al|a|para)\s+(?:el\s+)?((?:hoy|pasado ma[nñ]ana|ma[nñ]ana|lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)(?:\s+\w+)?)$", "move_task", "move"),
        (r"^(?:agend[aá](?:me)?|anot[aá] la tarea|nueva tarea|tarea)[:,]?\s+(?:la tarea[:,]?\s+|que\s+)?(.+)", "add_task", "text"),
        (r"^(?:cambi[aá]|pas[aá]) a (.+?)(?: en spotify)?$", "music_play", "query"),
        (r"^(?:pon[eé]|reproduc[ií]|tir[aá]|met[eé]) (?:m[uú]sica de |a |el tema |la canci[oó]n )?(.+?)(?: en spotify)?$", "music_play", "query"),
        (r"^(?:paus[aá]|par[aá] la m[uú]sica|pausa(?:r)? la m[uú]sica)$", "music_control", "what=pause"),
        (r"^(?:abr[ií]|abrime|abr[ií]me|ejecut[aá]|inici[aá])\s+(?:el |la |los |las )?(?:app |programa |aplicaci[oó]n )?([\w .+-]{2,30})$", "open_app", "name"),
        (r"^(?:siguiente|pr[oó]xima|pas[aá] (?:de )?(?:tema|canci[oó]n))$", "music_control", "what=next"),
        (r"^(?:segu[ií]|reanud[aá]|dale play)(?: la m[uú]sica)?$", "music_control", "what=resume"),
        (r"^(?:sub[ií] el volumen|m[aá]s fuerte)$", "volume", "direction=up"),
        (r"^(?:a c[oó]mo est[aá]|c[oó]mo est[aá]|cu[aá]nto est[aá]|precio del|cotizaci[oó]n del)? ?(?:el )?d[oó]lar(?: hoy| blue)?\??$", "dolar", None),
        (r"^(?:baj[aá] el volumen|m[aá]s bajo)$", "volume", "direction=down"),
    ]

    @staticmethod
    def _dia(w):
        """'hoy', 'mañana', 'pasado mañana' o un día de la semana ('miércoles', 'hoy miércoles') -> fecha."""
        hoy = dt.date.today(); w = w.lower().strip()
        if w.startswith("hoy"): return hoy
        if w.startswith("pasado"): return hoy + dt.timedelta(2)
        if w.startswith("ma"): return hoy + dt.timedelta(1)
        dias = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
        w = w.split()[0].replace("é", "e").replace("á", "a")
        if w in dias: return hoy + dt.timedelta((dias.index(w) - hoy.weekday()) % 7)
        return hoy

    def fast_route(self, text):
        import re
        t = text.strip().strip("¿¡?!.").strip()
        for pat, action, arg in self.FAST:
            m = re.match(pat, t, re.I)
            if not m: continue
            args = {}
            if arg and "=" in arg: k, v = arg.split("="); args[k] = v
            elif arg == "move":
                return {"action": "move_task", "args": {"task": (m.group(1) or m.group(2)).strip(),
                        "date": self._dia(m.group(3)).isoformat()}}
            elif arg: args[arg] = (m.group(1) or "").strip()
            if action == "task_status": args["status"] = "hecha"
            if action == "add_task":   # fecha simple al final: hoy, mañana, pasado mañana o un día de la semana
                hoy = dt.date.today(); txt = args["text"]
                dias = {"lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6}
                md = re.search(r"\s+(?:para\s+)?(?:el\s+)?(hoy|pasado ma[nñ]ana|ma[nñ]ana|lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)$", txt, re.I)
                d = hoy
                if md:
                    w = md.group(1).lower(); txt = txt[:md.start()]
                    if w.startswith("pasado"): d = hoy + dt.timedelta(2)
                    elif w.startswith("ma"): d = hoy + dt.timedelta(1)
                    elif w in dias: d = hoy + dt.timedelta((dias[w] - hoy.weekday()) % 7 or 7)
                args = {"text": txt.strip(" .,"), "date": d.isoformat()}
            return {"action": action, "args": args}
        return None

    @staticmethod
    def fix_args(a, args):
        """Corrige las claves que el modelo chico a veces inventa."""
        if a == "add_task" and not args.get("text"):
            args["text"] = args.get("task") or args.get("title") or args.get("name") or ""
        if a == "remember" and not args.get("fact"):
            args["fact"] = next((v for v in args.values() if isinstance(v, str)), "")
        if a == "music_play" and args.get("kind") in ("song", "cancion", "canción", "tema"): args["kind"] = "track"
        if a in ("task_status", "move_task", "delete_task") and not args.get("task"):
            args["task"] = args.get("text") or args.get("index") or ""
        return args

    PASOS = {   # qué decir mientras trabaja (se ve en vivo en la conversación y en la isla)
        "add_task": "Anotando la tarea", "task_status": "Actualizando la tarea", "move_task": "Moviendo la tarea",
        "list_tasks": "Revisando tus tareas", "reminder": "Programando el aviso", "timer": "Poniendo el timer",
        "morning_brief": "Armando tu resumen del día", "remember": "Guardándolo en la memoria",
        "cal_events": "Mirando tu calendario", "cal_add": "Agendando en el calendario",
        "mail_check": "Leyendo tus mails", "mail_reply": "Escribiendo el borrador", "web_answer": "Buscando en internet",
        "web_open": "Abriendo el navegador", "music_play": "Buscando en Spotify", "open_app": "Abriendo la app",
        "record_meeting": "Abriendo Granola", "stop_recording": "Cortando la grabación", "meeting_tasks": "Leyendo la nota de Granola",
        "meeting_now": "Leyendo la nota en vivo de Granola", "meeting_brief": "Buscando la reunión anterior en Granola",
        "meeting_note": "Anotándolo",
        "slack_send": "Preparando el mensaje en Slack", "find_file": "Buscando el archivo", "clean_desktop": "Mirando tu escritorio",
        "clipboard": "Leyendo lo que copiaste", "dolar": "Mirando el dólar", "chat": "Pensando la respuesta",
    }

    def _step(self, text):
        f = getattr(self.ui, "step", None)
        if f and text:
            try: f(text + "…")
            except Exception: pass

    CHARLA = [   # respuestas al instante para la charla más común (sin esperar a la IA)
        (r"^(?:che\s+)?(?:me (?:escuch[aá]s|o[ií]s)|est[aá]s ah[ií]|est[aá]s escuchando|hola\??\s*(?:me escuch[aá]s)?)(?: bien)?$",
         ["¡Sí, fuerte y claro! ¿Qué necesitás?", "Acá estoy, te escucho. ¿Qué hacemos?", "Te escucho perfecto. Decime."]),
        (r"^(?:hola|buenas|buen d[ií]a|buenas tardes|buenas noches|ey|epa)(?: mate)?$",
         ["¡Buenas! ¿Qué hacemos?", "¡Hola! Acá con el mate listo. ¿En qué te ayudo?", "¡Buenas! Decime."]),
        (r"^(?:qu[eé] onda|c[oó]mo (?:and[aá]s|est[aá]s|va|te va)|todo bien)(?: mate)?$",
         ["¡Todo bien por acá, cebándome uno! ¿Y vos?", "Bien, tranqui. ¿Qué necesitás?", "¡Joya! ¿En qué andás?"]),
        (r"^(?:gracias|muchas gracias|genial|joya|b[aá]rbaro|perfecto|dale gracias|buen[ií]simo)(?: mate)?$",
         ["¡De nada! Cualquier cosa, chiflame.", "¡Un placer!", "¡Para eso estoy! 🧉"]),
        (r"^(?:nada|nada m[aá]s|eso es todo|listo|chau|hasta luego|despu[eé]s te hablo)$",
         ["Dale, cualquier cosa acá estoy.", "Joya, me quedo en la isla.", "¡Chau! Acá ando."]),
    ]

    def charla(self, text):
        import random, re as _re
        t = text.strip().lower().strip("¿¡?!.,").strip()
        for pat, resp in self.CHARLA:
            if _re.match(pat, t):
                if ar.NIVEL == "sobrio": return resp[0].replace(" 🧉", "")
                return random.choice(resp)
        return None

    def handle(self, text):
        quick = self.charla(text)
        if quick:
            self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": quick}]
            return quick
        r = self.fast_route(text)
        raw = json.dumps(r, ensure_ascii=False) if r else None
        if not r:
            msgs = [{"role": "system", "content": self.system_prompt()}] + self.history[-6:] + \
                   [{"role": "user", "content": text}]
            self._step("Entendiendo el pedido")
            try:
                raw = llm(msgs)
            except Exception as e:
                return "✗ No llego a Ollama. ¿Está abierto? (" + str(e)[:60] + ")"
            try: r = json.loads(raw)
            except Exception: r = {"action": "chat", "reply": raw[:300]}
        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": raw}]
        a = r.get("action") or "chat"
        args = self.fix_args(a, r.get("args") or {})
        fn = getattr(self, "do_" + str(a), None)
        if a != "chat" or not (r.get("reply") or "").strip(): self._step(self.PASOS.get(a, "Haciéndolo"))
        try:
            out = fn(args) if fn else None
        except Exception as e:
            out = ar.di("error", e=e)
        save(self.d)
        if not out and (a == "chat" or not fn) and len((r.get("reply") or "").strip()) < 6:
            try:  # charla: respuesta libre usando lo que sabe del usuario
                mem = "; ".join(self.d["memory"]) or "nada"
                out = ask(text, f"Sos {NAME}, un asistente argentino que ama el mate. {ar.persona()} "
                                f"Respondé en 1 o 2 frases. Lo que sabés del usuario: {mem}.")
            except Exception:
                pass
        return out or r.get("reply") or "Listo"

    # ================= tareas semanales =================
    def find_task(self, ref):
        ref = str(ref or "").strip()
        if ref.isdigit() and self.last_list and 0 < int(ref) <= len(self.last_list):
            return self.last_list[int(ref) - 1]
        open_tasks = [t for t in self.d["tasks"] if t["status"] != "hecha"] or self.d["tasks"]
        texts = [t["text"].lower() for t in open_tasks]
        hit = next((i for i, x in enumerate(texts) if ref.lower() in x), None)
        if hit is None:
            m = difflib.get_close_matches(ref.lower(), texts, 1, 0.3)
            hit = texts.index(m[0]) if m else None
        if hit is None: raise RuntimeError(f"No encontré la tarea '{ref}'")
        return open_tasks[hit]

    def do_add_task(self, a):
        d = parse_date(a.get("date"))
        self.d["tasks"].append({"id": uuid.uuid4().hex[:8], "text": a["text"],
                                "date": d.isoformat() if d else None, "status": "pendiente"})
        self.ui.refresh_board()
        return ar.di("tarea", t=a["text"], f=(f" para {nice_date(d.isoformat())}" if d else ""))

    def do_task_status(self, a):
        t = self.find_task(a.get("task"))
        st = difflib.get_close_matches(str(a.get("status", "hecha")).lower(), STATUSES, 1, 0.3) or ["hecha"]
        t["status"] = st[0]; self.ui.refresh_board()
        return ar.di("hecha", t=t["text"]) if st[0] == "hecha" else ar.di("estado", t=t["text"], s=st[0])

    def do_move_task(self, a):
        t, d = self.find_task(a.get("task")), parse_date(a.get("date"))
        t["date"] = d.isoformat() if d else None; self.ui.refresh_board()
        return f"✓ Pasé '{t['text']}' a {nice_date(t['date'])}"

    def do_delete_task(self, a):
        t = self.find_task(a.get("task"))
        if not self.ui.confirm(f"¿Borro la tarea '{t['text']}'?"): return "Ok, la dejo"
        self.d["tasks"].remove(t); self.ui.refresh_board(); return "✓ Borrada"

    def tasks_in(self, rng):
        today = dt.date.today()
        monday = today - dt.timedelta(days=today.weekday())
        out = []
        for t in self.d["tasks"]:
            if t["status"] == "hecha": continue
            d = parse_date(t["date"])
            if rng == "today" and not (d and d <= today): continue
            if rng == "week" and not (d is None or d <= monday + dt.timedelta(days=6)): continue
            out.append(t)
        return sorted(out, key=lambda t: t["date"] or "9999")

    def do_list_tasks(self, a):
        rng = a.get("range") or "week"
        self.last_list = self.tasks_in(rng)
        if not self.last_list:
            return "No tenés nada pendiente " + {"today": "para hoy", "week": "esta semana"}.get(rng, "") + " 🧉"
        today = dt.date.today().isoformat()
        return "\n".join(f"{i+1}. {STATUS_ICON[t['status']]} {t['text']} · {nice_date(t['date'])}"
                         + (" ⚠ atrasada" if t["date"] and t["date"] < today else "")
                         for i, t in enumerate(self.last_list))

    def do_week_board(self, a):
        self.ui.open_board(); return "✓ Ahí tenés tu semana"

    # ================= memoria =================
    def do_remember(self, a):
        fact = a.get("fact", "").strip()
        if not fact: return "¿De qué me tengo que acordar?"
        self.d["memory"].append(fact); return ar.di("memoria", t=fact)

    def do_forget(self, a):
        about = str(a.get("about", "")).lower()
        hits = [m for m in self.d["memory"] if about and about in m.lower()]
        if not hits:
            m = difflib.get_close_matches(about, [x.lower() for x in self.d["memory"]], 1, 0.3)
            hits = [x for x in self.d["memory"] if m and x.lower() == m[0]]
        if not hits: return "No tenía nada anotado sobre eso"
        if not self.ui.confirm("¿Me olvido de esto?\n\n" + "\n".join(hits)): return "Ok, lo sigo recordando"
        self.d["memory"] = [m for m in self.d["memory"] if m not in hits]
        return f"✓ Olvidado ({len(hits)})"

    def do_memories(self, a):
        return "Esto sé de vos:\n" + "\n".join(f"• {m}" for m in self.d["memory"]) if self.d["memory"] \
            else "Todavía no me contaste nada. Decime 'acordate que…'"

    # ================= calendario =================
    def do_cal_events(self, a):
        d = parse_date(a.get("date")) or dt.date.today()
        ev = self.gmail.events(d)
        if not ev: return f"No tenés eventos {nice_date(d.isoformat())}"
        return f"Agenda de {nice_date(d.isoformat())}:\n" + "\n".join(f"{e['time']} {e['title']}" for e in ev)

    def do_cal_add(self, a):
        d = parse_date(a.get("date")) or dt.date.today()
        acc = self.gmail.add_event(a.get("title", "Evento"), d, a.get("time"), a.get("minutes") or 60)
        return f"✓ Agendé '{a.get('title')}' {nice_date(d.isoformat())}" + \
               (f" a las {a['time']}" if a.get("time") else "") + f" ({acc})"

    # ================= resumen de la mañana =================
    def do_morning_brief(self, a=None):
        h = dt.datetime.now().hour
        hola = ar.saludo()
        today = dt.date.today()
        parts = [hola]
        for d_, nombre in ar.feriados_proximos(7):
            parts.append(("🇦🇷 ¡Hoy es feriado: " if d_ == today else f"🇦🇷 Feriado el {nice_date(d_.isoformat())}: ") + nombre + ("!" if d_ == today else ""))
        overdue = [t for t in self.tasks_in("today") if t["date"] < today.isoformat()]
        due = [t for t in self.tasks_in("today") if t["date"] == today.isoformat()]
        if overdue: parts.append(f"⚠ Atrasadas ({len(overdue)}): " + ", ".join(t["text"] for t in overdue))
        parts.append(f"Hoy ({len(due)}): " + ", ".join(f"{t['text']} [{t['status']}]" for t in due) if due
                     else "Para hoy no tenés tareas agendadas.")
        week = [t for t in self.tasks_in("week") if t not in due and t not in overdue]
        if week: parts.append(f"En lo que queda de la semana: {len(week)} más.")
        try:
            if self.gmail.accounts():
                ev = self.gmail.events(today)
                if ev: parts.append("Calendario: " + ", ".join(f"{e['time']} {e['title']}" for e in ev))
                n = len(self.gmail.unread(n=20))
                if n: parts.append(f"Tenés {n} mail(s) sin leer.")
        except Exception:
            pass
        rem = [r for r in self.d["reminders"] if dt.datetime.fromtimestamp(r["ts"]).date() == today]
        if rem: parts.append("Recordatorios: " + ", ".join(
            f"{dt.datetime.fromtimestamp(r['ts']):%H:%M} {r['text']}" for r in rem))
        self.last_list = overdue + due + week
        self.d["last_brief"] = today.isoformat()
        return "\n".join(parts)

    # ================= recordatorios / foco / hábitos =================
    def do_reminder(self, a):
        if a.get("at"):
            hh, mm = map(int, str(a["at"]).split(":")[:2])
            when = dt.datetime.now().replace(hour=hh, minute=mm, second=0)
            if when < dt.datetime.now(): when += dt.timedelta(days=1)
        else:
            when = dt.datetime.now() + dt.timedelta(minutes=float(a.get("minutes") or 5))
        self.d["reminders"].append({"text": a.get("text", "Recordatorio"), "ts": when.timestamp()})
        return ar.di("recordar", h=f"{when:%H:%M}")

    def do_timer(self, a):
        self.do_reminder({"text": a.get("label") or "Terminó el timer", "minutes": a.get("minutes", 5)})
        return f"✓ Timer de {a.get('minutes', 5)} minutos"

    def do_focus(self, a):
        mins = float(a.get("minutes") or 25)
        self.focus_end, self.focus_break, self.focus_total = time.time() + 60 * mins, False, 60 * mins
        self.focus_last = time.time()
        return ar.di("foco", m=int(mins))

    def do_stop_focus(self, a):
        self.focus_end = None; return "Foco detenido"

    def do_habit_add(self, a):
        self.d["habits"][a["name"].lower()] = {"goal": int(a.get("goal") or 1), "log": {}}
        return f"✓ Hábito {a['name']} (meta {a.get('goal') or 1} por día)"

    def do_habit_log(self, a):
        hb = self.d["habits"].setdefault(a["name"].lower(), {"goal": 1, "log": {}})
        today = dt.date.today().isoformat()
        hb["log"][today] = hb["log"].get(today, 0) + int(a.get("amount") or 1)
        n, g = hb["log"][today], hb["goal"]
        return f"🎉 ¡Cumpliste la meta de {a['name']}! ({n}/{g})" if n == g else f"✓ {a['name']}: {n} de {g}"

    def do_habit_status(self, a):
        today, out = dt.date.today().isoformat(), []
        for name, hb in self.d["habits"].items():
            streak, day = 0, dt.date.today() if hb["log"].get(today, 0) >= hb["goal"] else dt.date.today() - dt.timedelta(days=1)
            while hb["log"].get(day.isoformat(), 0) >= hb["goal"]:
                streak += 1; day -= dt.timedelta(days=1)
            out.append(f"{name}: {hb['log'].get(today, 0)}/{hb['goal']} · racha {streak}🔥")
        return "\n".join(out) or "Sin hábitos todavía"

    # ================= escritorio / apps / web =================
    def do_clean_desktop(self, a):
        import mate_pc as pc
        opts = dict(pc.DEFAULTS, **CFG.get("desktop", {}))
        items = pc.plan(opts)
        if not items: return "Tu escritorio ya está ordenado 🧉"
        resumen = {}
        for _, rel in items: resumen[str(rel)] = resumen.get(str(rel), 0) + 1
        prev = "\n".join(f"• {n} → {d}" for d, n in sorted(resumen.items(), key=lambda x: -x[1]))
        if not self.ui.confirm(f"¿Ordeno {len(items)} archivos del escritorio?\n\n{prev}\n\nNo borro nada y podés deshacerlo."):
            return "Ok, lo dejo como está"
        self.d["last_cleanup"] = pc.apply(items)
        if opts.get("alinear"): pc.align_icons()
        return f"✓ Ordené {len(items)} archivos. Si no te gusta, decí 'deshacer'"

    def do_undo_cleanup(self, a):
        import mate_pc as pc
        n = pc.undo(self.d.get("last_cleanup"))
        self.d["last_cleanup"] = None
        return f"✓ Devolví {n} archivos a su lugar"

    def do_open_app(self, a):
        if self.apps is None: return "Estoy indexando tus programas, probá en unos segundos"
        return ar.di("abrir", a=self.apps.open(a["name"]))

    def do_web_open(self, a):
        sv.web_open(a["query"]); return f"✓ Buscando {a['query']}"

    def do_web_answer(self, a):
        import re as _re
        q = a.get("question") or a.get("query")
        if _re.search(r"clima|tiempo|temperatura|llov|lluvia|calor|fr[ií]o|pron[oó]stico", q, _re.I):
            place = _re.sub(r".*(?:en|de)\s+([A-ZÁÉÍÓÚ][\w ]+?)\??$", r"", q) if _re.search(r"en\s+[A-ZÁÉÍÓÚ]", q) else ""
            c = sv.config()
            res = sv.weather(place or c.get("coords") or c.get("ciudad", ""))
        else:
            res = sv.web_snippets(q)
        if not res: sv.web_open(q); return "No encontré nada claro, te lo abrí en el navegador"
        return ask(f"Pregunta: {q}\n\nResultados de búsqueda:\n" + "\n".join(res) +
                   "\n\nRespondé en 2 o 3 frases usando solo esa información.")

    def do_volume(self, a): sv.volume(a.get("direction", "up")); return "✓"
    def do_lock_pc(self, a): sv.lock_pc(); return "✓ Bloqueada"
    def do_dolar(self, a): return ar.dolar()

    def do_clipboard(self, a):
        txt = sv.clipboard_text()
        if not txt: return "No hay texto copiado"
        out = ask_long(f"{a.get('instructions') or 'Resumilo'}:\n\n{txt}")
        self.ui.copy(out); return out + "\n\n(✓ copiado al portapapeles)"

    def do_music_play(self, a):
        import mate_control
        if self.spotify.sp:   # API oficial configurada; si falla (sin dispositivo, etc.) usa la app
            try:
                return self.spotify.play(a.get("query", ""), a.get("kind", "track"))
            except Exception as e:
                log(f"spotify api: {e}")
        return mate_control.spotify_play(a.get("query", ""))

    def do_slack_send(self, a):
        import mate_control as C
        to, text = (a.get("to") or "").strip(), (a.get("text") or "").strip()
        if not to: return "¿A quién se lo mando por Slack?"
        f = None
        if a.get("file"):
            hits = C.find_file(a["file"])
            if not hits: return f"✗ No encontré ningún archivo que sea '{a['file']}'"
            f = hits[0]
        ok, msg = C.slack_prepare(to, text, f)
        if not ok: return msg
        resumen = f"Para: {to}\nMensaje: {text or '(sin texto)'}" + (f"\nArchivo: {f.name}" if f else "")
        if not self.ui.confirm(f"¿Se lo mando a {to} por Slack?\n" + resumen + "\nRevisá en Slack que sea el chat correcto.", ok="Mandar"):
            C.slack_discard(); return "Ok, no lo mandé y borré el borrador"
        undo = getattr(self.ui, "undo_window", None)
        if undo and not undo(f"Mandando a {to}…", 5):        # 5 s para arrepentirte
            C.slack_discard(); return "Ok, cancelado: no lo mandé"
        return C.slack_send_now()

    def do_find_file(self, a):
        import mate_control as C
        hits = C.find_file(a.get("query", ""))
        if not hits: return "No encontré nada parecido 🤔"
        os.startfile(hits[0])
        return "✓ Abrí " + hits[0].name + ("\nOtros parecidos: " + ", ".join(h.name for h in hits[1:4]) if len(hits) > 1 else "")

    def do_meeting_note(self, a):
        txt = (a.get("text") or "").strip()
        if not txt: return "¿Qué anoto?"
        rec = getattr(self.ui, "recording", None)
        self.d["tasks"].append({"id": uuid.uuid4().hex[:8], "text": txt[0].upper() + txt[1:], "date": None,
                                "status": "pendiente", **({"from": rec["title"]} if rec else {})})
        self.ui.refresh_board()
        return f"✓ Anotado{' para después de «' + rec['title'][:24] + '»' if rec else ''}: {txt}"

    def do_meeting_now(self, a):
        import mate_control as C
        title, body = C.granola_read_current()
        if not body: return "✗ No encontré una reunión abierta en Granola"
        out = ask_long(f"Notas en vivo de la reunión '{title}':\n\n{body}\n\nEn 3 a 5 viñetas cortas: qué se decidió hasta ahora, "
                       "qué quedó pendiente y quién se comprometió a qué. Si todavía no hay nada claro, decilo.",
                       "Resumís reuniones en español rioplatense, breve y concreto.", max_tokens=300)
        return out.strip()

    def do_meeting_brief(self, a):
        import mate_control as C
        title, body = C.granola_read_note(a.get("meeting") or None)
        if not body: return f"✗ No encontré una reunión con «{a.get('meeting')}» en Granola"
        out = ask_long(f"Notas de la última reunión '{title}':\n\n{body}\n\nArmá un machete para la próxima reunión en 4 a 6 viñetas: "
                       "temas que se hablaron, decisiones, y qué quedó pendiente (de quién).",
                       "Preparás machetes de reuniones en español rioplatense, breve y concreto.", max_tokens=220)
        return f"Machete de «{title[:50]}»:\n" + out.strip()

    def do_record_meeting(self, a):
        import mate_control
        res = mate_control.granola_record()
        if res.startswith("✓"):   # la isla muestra la grabación en curso con su contador
            ev = next((e for e in getattr(self.ui, "events_view", lambda: [])() if e.get("in") in ("ahora",) or
                       (e.get("upcoming") and e.get("in", "").startswith("en ") and int(e["in"].split()[1]) <= 15)), None)
            self.ui.recording = {"title": ev["title"] if ev else "Grabando reunión", "since": time.time()}
        return res

    def do_stop_recording(self, a):
        import mate_control as C
        res = C.granola_stop()
        self.ui.recording = None
        if not res.startswith("✓"): return res
        self.ui.alert("✓ Corté la grabación. Le pido a Granola las notas y te busco tus tareas…", False)
        C.granola_generate_notes()
        return self.do_meeting_tasks({})

    def do_meeting_tasks(self, a):
        import mate_control as C, re as _re
        title, body = C.granola_read_note(a.get("meeting"))
        if not body:
            time.sleep(2); title, body = C.granola_read_note(a.get("meeting"))   # Granola a veces tarda en cambiar de pantalla
        if not body: return "✗ No pude leer la nota de Granola"
        import mate_servicios
        me = (mate_servicios.config().get("user_name") or "").strip() or "yo"
        hoy = dt.date.today()
        dias = ", ".join(f"{DIAS[(hoy + dt.timedelta(i)).weekday()]}={(hoy + dt.timedelta(i)).isoformat()}" for i in range(8))
        nombre = me.split()[0].lower()
        alias = [nombre] + ([nombre[:4]] if len(nombre) >= 5 else []) + ["yo ", " i will", "i'll"]
        mias = [l.strip(" •◦-") for l in body.split("\n") if any(x in l.lower() for x in alias)]
        if mias:   # lo más preciso: convertir en tareas solo las líneas que te nombran
            prompt = (f"Hoy es {DIAS[hoy.weekday()]} {hoy.isoformat()} ({dias}). Reunión: '{title}'.\n"
                      f"Estas líneas de las notas mencionan a {me}:\n" + "\n".join("- " + l for l in mias) +
                      f"\n\nConvertí en tarea CADA línea donde {me} tiene que hacer algo (si una línea tiene varias acciones, "
                      "separalas). Ignorá las líneas que no son tareas suyas. Fecha SOLO si se menciona un día o plazo. "
                      'Respondé SOLO JSON: {"tareas":[{"texto":"verbo en infinitivo + qué, en español","fecha":"YYYY-MM-DD o vacío"}]}')
        else:
            prompt = (f"Hoy es {DIAS[hoy.weekday()]} {hoy.isoformat()} ({dias}). Notas de la reunión '{title}':\n\n{body}\n\n"
                      f"Extraé SOLO las tareas o compromisos asignados a {me} (el usuario). Ignorá las de otras personas. "
                      "Fecha SOLO si se menciona un día o plazo. "
                      'Respondé SOLO JSON: {"tareas":[{"texto":"verbo en infinitivo + qué","fecha":"YYYY-MM-DD o vacío"}]}')
        out = ask_long(prompt, "Extraés tareas de reuniones. Respondés solo JSON válido.", max_tokens=600, temp=0)
        m = _re.search(r"\{.*\}", out, _re.S)
        try: tareas = json.loads(m.group(0))["tareas"] if m else []
        except Exception: tareas = []
        tareas = [t for t in tareas if (t.get("texto") or "").strip()]
        if not tareas: return f"De '{title}' no saqué tareas para vos 🙌"
        lista = "\n".join(f"Tarea: {t['texto']}" + (f" ({nice_date(t['fecha'])})" if parse_date(t.get("fecha")) else "") for t in tareas)
        n = len(tareas)
        if not self.ui.confirm(f"Encontré {n} tarea{'s' if n > 1 else ''} para vos en «{title[:40]}». ¿Las sumo?\n{lista}",
                               ok="Agregar todas"):
            return "Ok, no sumé nada"
        for t in tareas:
            d = parse_date(t.get("fecha"))
            self.d["tasks"].append({"id": uuid.uuid4().hex[:8], "text": t["texto"], "date": d.isoformat() if d else None,
                                    "status": "pendiente", "from": title})
        self.ui.refresh_board()
        return f"✓ Sumé {len(tareas)} tareas de '{title[:40]}' a tu lista"

    def do_app_click(self, a):
        import mate_control; return mate_control.app_click(a.get("app", ""), a.get("button", ""))

    def do_app_type(self, a):
        import mate_control; return mate_control.app_type(a.get("app", ""), a.get("text"), a.get("keys"))
    def do_music_control(self, a): self.spotify.control(a.get("what", "pause")); return "✓"

    # ================= mail =================
    def do_mail_connect(self, a):
        alias = a.get("alias") or self.ui.ask_text("Nombre para esta cuenta (ej: personal, trabajo):")
        if not alias: return "Cancelado"
        return f"✓ Conecté {self.gmail.connect(alias)} como '{alias}' (Gmail + Calendar)"

    def do_mail_check(self, a):
        self.mails = self.gmail.unread(a.get("account") or None)
        if not self.mails: return "No tenés mails sin leer 🙌"
        return f"Tenés {len(self.mails)} sin leer:\n" + "\n".join(
            f"{i+1}. [{m['account']}] {m['from'].split('<')[0].strip()}: {m['subject']}"
            for i, m in enumerate(self.mails))

    def _mail(self, a):
        if not self.mails: self.mails = self.gmail.unread()
        return self.mails[int(a.get("index") or 1) - 1]

    def do_mail_read(self, a):
        m = self._mail(a); return f"De {m['from']}\n{m['subject']}\n\n{m['snippet']}"

    def do_mail_reply(self, a):
        m = self._mail(a)
        draft = ask(f"Escribí la respuesta a este mail. Indicaciones: {a.get('instructions') or 'respondé cordialmente'}.\n"
                    f"De: {m['from']}\nAsunto: {m['subject']}\nTexto: {m['snippet']}\n\n"
                    "Lo que sabés del usuario: " + "; ".join(self.d["memory"]) +
                    "\nSolo el cuerpo del mail, sin asunto.")
        choice = self.ui.mail_dialog(m, draft)
        if choice is None: return "No envié nada"
        body, mode = choice
        undo = getattr(self.ui, "undo_window", None)
        quien = m["from"].split("<")[0].strip().strip('"')[:24]
        if mode == "send" and undo and not undo(f"Mandando a {quien}…", 5):   # 5 s para arrepentirte
            return "Ok, cancelado: no mandé el mail"
        to = self.gmail.send(m, body, draft_only=(mode == "draft"))
        return f"✓ {'Borrador guardado' if mode == 'draft' else 'Enviado'} a {to}"

    # ================= reloj =================
    def _meeting_watch(self):
        """5 min antes de una reunión te avisa; cuando empieza, te ofrece grabarla con Granola."""
        if not CFG.get("avisos_reunion", True): return
        evs = [e for e in (getattr(self.ui, "events_view", lambda: [])() or []) if e.get("date") == dt.date.today().isoformat()
               and e.get("time", "").count(":") == 1]
        seen = self.__dict__.setdefault("_meet_seen", set())
        now = dt.datetime.now()
        for e in evs:
            h, m = map(int, e["time"].split(":"))
            mins = (dt.datetime.combine(now.date(), dt.time(h, m)) - now).total_seconds() / 60
            k5, k0 = ("5", e["time"], e["title"]), ("0", e["time"], e["title"])
            k10 = ("10", e["time"], e["title"])
            if 8 <= mins <= 11 and k10 not in seen and CFG.get("machete_auto", True):
                seen.add(k10)   # 10 min antes: preparo el machete de la reunión anterior con el mismo nombre
                threading.Thread(target=self._auto_brief, args=(e["title"],), daemon=True).start()
            if 3.5 <= mins <= 5.5 and k5 not in seen:
                seen.add(k5)
                listo = self.__dict__.get("_brief_ready", {}).get(e["title"])
                self.ui.alert(f"📅 En 5 min: {e['title']}" + (" · tu machete está en el panel" if listo else ""), True)
            elif -2 <= mins <= 0.5 and k0 not in seen and not getattr(self.ui, "recording", None):
                seen.add(k0)
                def offer(e=e):
                    if self.ui.confirm(f"Empieza «{e['title'][:40]}». ¿La grabo con Granola?\nCuándo: {e['time']}"
                                       + (f"–{e['end']}" if e.get("end") else "") + "\nTe armo las tareas cuando termine.", ok="Grabar"):
                        self.ui.show(self.do_record_meeting({}), speak=False)
                threading.Thread(target=offer, daemon=True).start()

    def _auto_brief(self, title):
        """Machete automático: busca en Granola la reunión anterior parecida y lo deja en la conversación."""
        import re as _re
        hint = _re.sub(r"[|·\-–:]+", " ", title).strip()
        hint = max(hint.split(), key=len) if hint else title
        res = self.do_meeting_brief({"meeting": hint})
        if res.startswith("✗"): return
        self.__dict__.setdefault("_brief_ready", {})[title] = True
        show = getattr(self.ui, "show", None)
        if show: show("📋 Para tu próxima reunión (" + title[:40] + "):\n" + res, speak=False)

    def _granola_watch(self):
        """Detecta solo cuando Granola graba (aunque la arranques a mano) y, al terminar, te propone tus tareas."""
        import comtypes, mate_control as C
        comtypes.CoInitialize()
        on, off_since, mine = 0, None, False
        while True:
            time.sleep(15)
            try: rec = C.granola_capturing()
            except Exception as e:
                log(f"granola watch: {e}"); continue
            if rec:
                on += 1; off_since = None
                if on >= 2 and self.ui.recording: mine = True   # la arrancó Mate: igual sigo su final
                elif on == 2:                                     # 30 s seguidos grabando: es una reunión
                    mine = True
                    self.ui.recording = {"title": "Granola está grabando", "since": time.time() - 30}
                    self.ui.notch("Veo que Granola está grabando. Al terminar te busco tus tareas", "", 6)
                    log("granola: empezó a grabar")
            else:
                on = 0
                if self.ui.recording and mine:
                    off_since = off_since or time.time()
                    if time.time() - off_since > 60:      # 1 min sin micrófono: terminó (no una pausa)
                        mine = False; off_since = None; self.ui.recording = None
                        log("granola: terminó la grabación")
                        self.ui.notch("Terminó la reunión. Espero las notas de Granola y te busco tus tareas", "", 8)
                        def after():
                            time.sleep(45)                # Granola arma las notas solo al cortar
                            return self.do_meeting_tasks({})
                        self.ui.background(after)

    def tick(self):
        now = time.time()
        if not getattr(self, "_gw", False):
            self._gw = True; threading.Thread(target=self._granola_watch, daemon=True).start()
        if int(now) % 30 == 0:
            try: self._meeting_watch()
            except Exception: pass
        due = [r for r in self.d["reminders"] if r["ts"] <= now]
        if due:
            self.d["reminders"] = [r for r in self.d["reminders"] if r["ts"] > now]; save(self.d)
            for r in due: self.ui.alert("🔔 " + r["text"])
        if dt.datetime.now().hour >= 5 and self.d.get("last_brief") != dt.date.today().isoformat():
            self.d["last_brief"] = dt.date.today().isoformat()  # primer arranque del día
            self.ui.background(self.do_morning_brief)
        if self.focus_end:
            if not self.focus_break:  # suma minutos de enfoque del día
                log = self.d.setdefault("focus_log", {}); k = dt.date.today().isoformat()
                log[k] = round(log.get(k, 0) + (now - getattr(self, "focus_last", now)) / 60, 2)
            self.focus_last = now
            left = self.focus_end - now
            if left <= 0:
                if self.focus_break:
                    self.focus_end = None; self.ui.alert(ar.di("fin_descanso"))
                else:
                    self.focus_break, self.focus_end, self.focus_total = True, now + 300, 300
                    self.ui.alert(ar.di("descanso"))
            else:
                return f"{'☕' if self.focus_break else '🎯'} {int(left // 60):02d}:{int(left % 60):02d}"
        if CFG.get("mail_alerts", True) and now - self.last_mail_check > 300 and self.gmail.accounts():
            self.last_mail_check = now
            threading.Thread(target=self._mail_watch, daemon=True).start()
        return None

    URGENTE = ("urgente", "urgent", "asap", "hoy", "importante", "contrato", "factura", "pago", "vence",
               "deadline", "reunión", "reunion", "propuesta", "presupuesto")

    def mail_score(self, m):
        """Qué tan importante es un mail: contactos VIP, palabras clave y nada de newsletters."""
        frm, txt = m["from"].lower(), (m["subject"] + " " + m["snippet"]).lower()
        if any(x in frm for x in ("noreply", "no-reply", "newsletter", "notifications", "mailer", "marketing")):
            return 0
        vip = [v.lower() for v in CFG.get("vip", [])] + [w.lower() for f in self.d.get("memory", [])
                                                         for w in f.split() if w[:1].isupper() and len(w) > 2]
        score = 1
        if any(v and v in frm for v in vip): score += 2
        if any(k in txt for k in self.URGENTE): score += 1
        return score

    def _mail_watch(self):
        try:
            new = self.gmail.unread(n=10)
            seen = set(self.d.get("seen_mails", []))
            fresh = [m for m in new if m["id"] not in seen]
            self.d["seen_mails"] = list(seen | {m["id"] for m in new})[-300:]
            if not (fresh and seen): return
            nivel = CFG.get("mail_nivel", "importantes")     # todos | importantes | nada
            if nivel == "nada": return
            top = sorted(fresh, key=self.mail_score, reverse=True)
            if nivel == "importantes": top = [m for m in top if self.mail_score(m) >= 2]
            if not top: return
            m = top[0]
            quien = m["from"].split("<")[0].strip().strip('"')
            try:
                resumen = ask(f"Resumí en una frase corta (máx 12 palabras) este mail para un aviso:\nDe: {quien}\n"
                              f"Asunto: {m['subject']}\n{m['snippet']}")
            except Exception:
                resumen = m["subject"]
            extra = f" (+{len(top) - 1} más)" if len(top) > 1 else ""
            self.ui.alert(f"✉ {quien}: {resumen.strip()[:90]}{extra}", False)
        except Exception:
            pass


# ======================= arranque =======================
if __name__ == "__main__":
    from mate_ui import WebUI
    WebUI(Agent, Speaker, Listener, CFG, WAKE, NAME).start()
