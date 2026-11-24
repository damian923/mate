"""
Mate - asistente de escritorio 100% local (Windows).
IA: Ollama (llama3.2:3b por defecto). Voz: Vosk + pyttsx3 (offline). Doble aplauso para activar.
"""
import difflib, json, os, re, shutil, subprocess, sys, threading, time, uuid, datetime as dt
from pathlib import Path
if sys.stdout is None: sys.stdout = open(os.devnull, "w")   # con pythonw no hay consola: que las librerías
if sys.stderr is None: sys.stderr = open(os.devnull, "w")   # que imprimen progreso no se caigan
import urllib.request

import mate_servicios as sv
import mate_argento as ar
from mate_audio import Listener, Speaker, log, log_error


def _re_bloq(text):
    import re
    return bool(re.search(r"bloque|lock", text or "", re.I))

CFG = sv.config()
# Perfil "liviano" (compus con 8 GB y sin placa de video): modelo local chico, un solo modelo para todo,
# sin Whisper (sólo Parakeet) y la IA se descarga de memoria enseguida. Se elige con "perfil" en config.json.
LIVIANO = CFG.get("perfil") == "liviano"
MODEL = os.environ.get("MATE_MODEL", CFG.get("model", "llama3.2:1b" if LIVIANO else "llama3.2:3b"))
MODEL_LONG = CFG.get("model_long", MODEL if LIVIANO else "hf.co/unsloth/Qwen3.5-2B-GGUF:Q4_K_M")   # reuniones y textos largos
KEEP = CFG.get("keep_alive", "5m" if LIVIANO else "2h")   # cuánto queda la IA local en memoria
# IA local para entender pedidos: Qwen 2.5 1.5B sigue mucho mejor el formato JSON que llama3.2:3b y es más rápida
MODEL_ROUTER = CFG.get("model_router", "qwen2.5:1.5b")
WAKE = CFG.get("wake_word", "mate")
NAME = CFG.get("name", "Mate")
ar.NIVEL = CFG.get("argentinidad", "normal")
OLLAMA = "http://localhost:11434/api/chat"
HOME = Path.home()
DATA_DIR = HOME / ".mate"
# Modo demo (para mostrar Mate a clientes): datos aparte, así nunca se mezcla con tus tareas reales
DEMO = bool(CFG.get("demo"))
DATA_FILE = DATA_DIR / ("data_demo.json" if DEMO else "data.json")
DEMO_DIR = Path(__file__).resolve().parent / "demo"
DESKTOP = next((p for p in [HOME / "Desktop", HOME / "OneDrive" / "Escritorio", HOME / "Escritorio",
                            HOME / "OneDrive" / "Desktop"] if p.exists()), HOME / "Desktop")
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
STATUSES = ["pendiente", "en curso", "esperando", "hecha"]
STATUS_ICON = {"pendiente": "○", "en curso": "◐", "esperando": "⏸", "hecha": "✔"}
STATUS_COLOR = {"pendiente": "#e0e0e0", "en curso": "#4fc3f7", "esperando": "#fbbf24", "hecha": "#66bb6a"}


_save_lock = threading.Lock()


def load():
    """Lee tus datos. Si data.json está roto (p. ej. se cortó a mitad de un guardado) usa la copia .bak
    y guarda el roto aparte. NUNCA arranca vacío si había datos: antes eso borraba todas tus tareas."""
    d = None
    for f in (DATA_FILE, DATA_FILE.with_suffix(".json.bak")):
        if not f.exists(): continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if f != DATA_FILE: log(f"datos: data.json estaba roto, recuperé de {f.name}")
            break
        except Exception as e:
            log(f"datos: no pude leer {f.name} ({e})")
            try: shutil.copy(f, f.with_name(f.name + f".roto-{int(time.time())}"))   # se guarda para no perderlo
            except Exception: pass
    d = d if isinstance(d, dict) else {}
    if DEMO and not d.get("projects"):   # demo: arranca con proyectos de ejemplo
        d["projects"] = [
            {"name": "Agente Copilot", "description": "Agente en Copilot Studio para atención al cliente de DirectTV", "active": True},
            {"name": "Capacitación DirectTV", "description": "Capacitación en IA de 4 bloques para el equipo de DirectTV", "active": True},
            {"name": "Jurisprudencia RAG", "description": "Motor de búsqueda de jurisprudencia con RAG y Pinecone", "active": True}]
    for k, v in {"tasks": [], "reminders": [], "habits": {}, "memory": [], "projects": [], "last_cleanup": None}.items():
        d.setdefault(k, v)
    for t in d["tasks"]:  # migra tareas viejas
        t.setdefault("id", uuid.uuid4().hex[:8]); t.setdefault("date", None)
        t.setdefault("status", "hecha" if t.pop("done", False) else "pendiente")
    return d


def save(d):
    """Guardado seguro: un solo hilo a la vez, escribe a un temporal y lo reemplaza de golpe (si se corta
    a mitad, data.json queda como estaba), y deja la versión anterior como data.json.bak."""
    DATA_DIR.mkdir(exist_ok=True)
    with _save_lock:
        txt = json.dumps(d, ensure_ascii=False, indent=1)
        tmp = DATA_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(txt); f.flush(); os.fsync(f.fileno())
        if DATA_FILE.exists():
            try: os.replace(DATA_FILE, DATA_FILE.with_suffix(".json.bak"))
            except Exception: pass
        os.replace(tmp, DATA_FILE)


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
- add_task {"text","date","project"}  (date YYYY-MM-DD o vacío; "el jueves" = el próximo jueves de la tabla; project = uno de SUS PROYECTOS si corresponde, si no vacío)
- project_add {"name","description"}  (el usuario cuenta un proyecto suyo y de qué trata; también para sumar detalles a uno existente)
- project_list {} | project_delete {"name"}
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
- whatsapp_send {"to","text","file"}  (mandar un WhatsApp; to = contacto como está agendado; file opcional)
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
- volume {"direction": up|down|mute}
- close_app {"name"}             (cerrar una app: "cerrá Chrome")
- lock_pc {}                     (SOLO si pide "bloqueá la compu")
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
"mandale un whatsapp a mamá que llego tarde" -> {"action":"whatsapp_send","args":{"to":"mamá","text":"Llego tarde"}}
"agendame una reunión con Lara el viernes a las 15" -> {"action":"cal_add","args":{"title":"Reunión con Lara","date":"<fecha del viernes>","time":"15:00","minutes":60}}
"pasá al siguiente tema" -> {"action":"music_control","args":{"what":"next"}}
"tengo un proyecto que se llama DM, es una agencia de marketing que hace campañas de mail" -> {"action":"project_add","args":{"name":"DM","description":"agencia de marketing que hace campañas de mail"}}
"qué proyectos tengo" -> {"action":"project_list","args":{}}
"abrí granola y empezá a grabar" ->{"action":"record_meeting","args":{}}
"contame un chiste" -> {"action":"chat","args":{},"reply":"¿Sabés por qué el mate nunca se pelea? Porque siempre está en ronda."}
"cómo estás" -> {"action":"chat","args":{},"reply":"¡Bien! Acá, cebándome uno. ¿Vos?"}
"¿cómo se llama mi hermana?" -> {"action":"chat","args":{},"reply":"Sofi, me lo contaste vos."}
Nunca uses otra clave que "text" para add_task. Si es una pregunta sobre algo que ya sabés del usuario, usá chat.

Hoy es {today}. Ahora son las {now}. Próximos días:
{days}
Cuentas de Google conectadas: {accounts}.
Lo que sabés del usuario:
{memory}

SUS PROYECTOS (para clasificar tareas y entender de qué habla):
{projects}

Tareas abiertas del usuario (para referirte a una usá su texto exacto en "task"):
{tasks}

CÓMO ENTENDER BIEN:
- El usuario habla rioplatense, con muletillas y frases a medias; el texto puede venir de un dictado con errores
  ("yerga" = yerba). Interpretá la INTENCIÓN, usando sus proyectos, sus tareas y su memoria como contexto.
- Si no estás seguro entre dos acciones o falta un dato clave (a quién, qué día), NO adivines: usá chat con
  "reply" = una pregunta corta y concreta ("¿Lo anoto como tarea para mañana o como nota de la reunión?").
- Si es una frase sin pedido (un comentario, "me parece bien", algo dicho a otra persona), respondé
  {"action":"chat","args":{},"reply":""} (no hagas nada).
- Nunca prometas cosas que no podés hacer ni hagas preguntas de relleno.
- Si el pedido trae "¿día?" es que no se escuchó bien el día: NO agendes, preguntá en reply "¿Para qué día?".

VARIAS ACCIONES: si el pedido implica varios cambios (ej. "pasá todas las de hoy a mañana", "repartilas entre
mañana y el viernes", "marcá como hechas las de DM"), respondé {"actions": [{"action": "...", "args": {...}}, ...]}
con una acción por tarea, usando las fechas exactas YYYY-MM-DD de la lista de días. Para repartir, alterná o
agrupá con criterio y explicalo corto en "reply"."""


_ollama_ok = 0


def ensure_ollama():
    """Si Ollama (la IA local) está apagado, lo prende y espera a que responda (hasta ~20 s)."""
    global _ollama_ok
    if time.time() - _ollama_ok < 60: return
    base = OLLAMA.split("/api/")[0]
    def up():
        try: urllib.request.urlopen(base + "/api/tags", timeout=2).read(); return True
        except Exception: return False
    if not up():
        log("ollama: estaba apagado, lo prendo")
        try:
            if sv.IS_MAC: subprocess.Popen(["brew", "services", "start", "ollama"])
            else: subprocess.Popen(["ollama", "serve"], creationflags=0x08000000)
        except Exception as e:
            log(f"ollama: no pude prenderlo ({e})"); return
        for _ in range(20):
            time.sleep(1)
            if up(): break
    _ollama_ok = time.time()


def llm(messages, as_json=True, temp=0.2, model=None, schema=None, ctx=3072):
    ensure_ollama()
    body = {"model": model or MODEL, "messages": messages, "stream": False, "keep_alive": KEEP,
            "options": {"temperature": temp, "num_ctx": ctx, "num_predict": 120 if as_json else 160,
                        "num_thread": max(2, (os.cpu_count() or 4) - 1)}}   # contexto justo + todos los núcleos menos uno
    if as_json: body["format"] = schema or "json"   # schema: JSON forzado (la IA no puede devolver otra cosa)
    req = urllib.request.Request(OLLAMA, json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["message"]["content"]


def ask_long(prompt, system="Respondé en español rioplatense, claro y concreto.", max_tokens=500, temp=0.2):
    """Para textos largos (reuniones, documentos): Qwen3.5 2B de Unsloth, 262K de contexto.
    Se carga solo cuando hace falta y se descarga a los 3 minutos para liberar memoria."""
    key = (sv.config().get("gemini_key") or "").strip()
    if key:   # con Gemini (autorizado por el usuario): segundos en vez de minutos
        try:
            import mate_gemini as G
            out = G.complete(key, sv.config().get("gemini_model"), system, prompt, max_tokens)
            if out: return out
        except Exception as e:
            log(f"gemini largo: {e} (uso la IA local)")
    ensure_ollama()
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    need = int(len(prompt) / 3) + max_tokens + 512
    ctx = 16384 if need <= 16384 else 65536          # tamaños fijos: así no recarga el modelo cada vez
    body = {"model": MODEL_LONG, "messages": msgs, "stream": False, "keep_alive": "2m" if LIVIANO else "10m", "think": False,
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
        # En el perfil liviano con Gemini, la IA local es sólo de respaldo: no la cargo en memoria al arrancar
        try:
            import mate_intencion; mate_intencion.precalentar()   # clasificador rápido (la 1ª vez baja ~220 MB)
        except Exception as e:
            log(f"clasificador no disponible: {e}")
        if not (LIVIANO and (sv.config().get("gemini_key") or "").strip()):
            threading.Thread(target=self._warm_up, daemon=True).start()

    def save(self): save(self.d)

    @staticmethod
    def _warm_up():
        """Carga el modelo en memoria al arrancar para que el primer pedido sea rápido."""
        for _ in range(10):
            try:
                llm([{"role": "user", "content": "hola"}], as_json=False, model=MODEL_ROUTER)   # el que entiende pedidos
                return
            except Exception: time.sleep(6)   # Ollama todavía arrancando con Windows

    def route_local(self, text):
        """Sin nube: el clasificador elige las 3 acciones más probables (~30 ms) y la IA local sólo decide entre
        esas (+ charla) con JSON forzado. Instrucciones ~5 veces más cortas que la lista completa."""
        import mate_intencion as I
        cands = I.candidatas(text, 3)
        if not cands: return None                       # clasificador todavía cargando: camino de siempre
        seg = I.seguro(text)
        if seg: cands = [seg]                           # está seguro: la IA sólo completa los datos de esa acción
        acciones = [a for a in dict.fromkeys(cands + ([] if seg and seg != "chat" else ["chat"]))]
        lineas = {m.group(1): m.group(0) for m in re.finditer(r"^- (\w+) .*$", SYSTEM, re.M)}
        # campos exactos de cada acción (de la lista de acciones): así no inventa claves
        campos = {}
        for a in acciones:
            m = re.search(r"\{([^}]*)\}", lineas.get(a, ""))
            campos[a] = re.findall(r'"(\w+)"', m.group(1)) if m else []
        lista = "\n".join(lineas.get(a, f"- {a} {{}}") for a in acciones)
        hoy = dt.date.today()
        dias = ", ".join(f"{DIAS[(hoy + dt.timedelta(i)).weekday()]}={(hoy + dt.timedelta(i)).isoformat()}" for i in range(8))
        extra = ""
        if any(a in acciones for a in ("task_status", "move_task", "delete_task", "list_tasks")):
            abiertas = [t for t in self.d["tasks"] if t["status"] != "hecha"][:25]
            extra += "\nTareas abiertas (usá el texto exacto en \"task\"):\n" + "\n".join(f"- {t['text']}" for t in abiertas)
        if any(a in acciones for a in ("add_task", "project_add")) and self.d.get("projects"):
            extra += "\nSus proyectos: " + ", ".join(p["name"] for p in self.d["projects"])
        sistema = (f"Sos {NAME}, asistente argentino. Elegí UNA acción para el pedido y completá sus datos.\n"
                   f"Acciones posibles:\n{lista}\n"
                   f"Si el pedido no es ninguna de esas o es un comentario, usá chat con una respuesta corta en reply.\n"
                   f"Hoy es {DIAS[hoy.weekday()]} {hoy.isoformat()} ({dias}). Fechas en YYYY-MM-DD.{extra}\n"
                   'Respondé SOLO JSON: {"action": "...", "args": {...}, "reply": "..."}')
        todos = sorted({c for a in acciones for c in campos.get(a, [])})
        props = {c: ({"type": "integer"} if c in ("minutes", "amount", "goal", "index") else {"type": "string"}) for c in todos}
        schema = {"type": "object", "properties": {"action": {"type": "string", "enum": acciones},
                                                   "args": {"type": "object", "properties": props, "additionalProperties": False},
                                                   "reply": {"type": "string"}},
                  "required": ["action", "args"]}
        sistema += ("\nUsá SOLO los datos que dijo el usuario: no inventes mails, nombres ni archivos. "
                    "Un campo que no se mencionó, dejalo afuera.")
        t0 = time.time()
        try:
            raw = llm([{"role": "system", "content": sistema}, {"role": "user", "content": text}],
                      model=MODEL_ROUTER, schema=schema, ctx=2048)
        except Exception as e:
            log(f"route_local: {e}"); return None
        try: r = json.loads(raw)
        except Exception: return None
        # Fechas: las calcula Mate con el calendario (la IA chica se equivoca de día). "el viernes", "mañana", "lunes"…
        a = r.get("action"); args = r.setdefault("args", {})
        md = re.search(r"\b(hoy|pasado ma[nñ]ana|ma[nñ]ana|lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\b", text, re.I)
        if a in ("add_task", "move_task", "cal_add", "cal_events") and md:
            args["date"] = self._dia(md.group(1)).isoformat()
            if a == "add_task" and args.get("text"):   # el día no va en el texto de la tarea
                args["text"] = re.sub(r"\s*(?:para\s+)?(?:el\s+)?" + re.escape(md.group(1)) + r"\b|\s*\d{4}-\d{2}-\d{2}", "",
                                      args["text"], flags=re.I).strip(" ,.")
        if a == "move_task" and args.get("task") and md:   # "DM al viernes" -> "DM"
            args["task"] = re.sub(r"\s*(?:al|a|para)?\s*(?:el\s+)?" + re.escape(md.group(1)) + r"\b.*$", "", args["task"], flags=re.I).strip()
        log(f"local: {cands} -> {a} ({time.time() - t0:.1f}s)")
        return r

    TEMA_TAREAS = re.compile(r"tarea|pas[aá]|mov[eé]|marc[aá]|borr[aá]|hech|termin|list[oa]|semana|hoy|mañana|"
                             r"proyecto|pendiente|esperando|curso|repart|reprogram|atrasad|qu[eé] tengo", re.I)

    def system_prompt(self, text=None, compacto=False):
        """Instrucciones para la IA. compacto=True (IA local, lenta en CPU): sin la lista de tareas salvo que el
        pedido hable de tareas, y con menos ejemplos. Pasa de ~2.400 a ~900 tokens: varios segundos menos."""
        today = dt.date.today()
        days = "\n".join(f"{DIAS[(today + dt.timedelta(i)).weekday()]} = {(today + dt.timedelta(i)).isoformat()}"
                         for i in range(8))
        mem = "\n".join(f"- {m}" for m in self.d["memory"]) or "(nada todavía)"
        abiertas = [t for t in self.d["tasks"] if t["status"] != "hecha"][:40]
        if compacto and not (text and self.TEMA_TAREAS.search(text)):
            abiertas = []                       # no hace falta: el pedido no es sobre tareas
        tasks = "\n".join(f"- {t['text']} | {t.get('date') or 'sin fecha'} | {t['status']}" +
                          (f" | proyecto: {t['from']}" if t.get("from") else "") for t in abiertas) or "(ninguna)"
        base = SYSTEM
        if compacto:   # de los ejemplos dejo los 6 primeros (los más comunes)
            a, _, resto = base.partition("Ejemplos:\n")
            ej, _, cola = resto.partition("Nunca uses otra clave")
            base = a + "Ejemplos:\n" + "\n".join(ej.strip().split("\n")[:6]) + "\nNunca uses otra clave" + cola
        return (base.replace("{tasks}", tasks).replace("{projects}", self.projects_text()).replace("{name}", NAME).replace("{persona}", ar.persona()).replace("{today}", f"{DIAS[today.weekday()]} {today.isoformat()}")
                .replace("{now}", dt.datetime.now().strftime("%H:%M")).replace("{days}", days)
                .replace("{accounts}", ", ".join(self.gmail.accounts()) or "ninguna").replace("{memory}", mem))

    # Atajos fijos: los pedidos más comunes no pasan por la IA (instantáneo y sin errores)
    FAST = [
        (r"^(?:che\s+)?(?:acordate|acuérdate|acordá|recordá)\s+(?:de\s+)?(?:que\s+)?(.+)", "remember", "fact"),
        (r"^(?:pon[eé](?:me)?|arranc[aá]|activ[aá]|tirame)?\s*(?:un |una )?(?:timer|temporizador|cron[oó]metro|alarma) (?:de |por )?(\d+) ?(?:min|minutos)?$", "timer", "minutes"),
        (r"^(?:pas[aá](?:me)? (?:al |a la )?(?:siguiente|pr[oó]xim[oa])(?: tema| canci[oó]n)?|siguiente (?:tema|canci[oó]n)|salte[aá](?:la)?(?: el tema| la canci[oó]n)?|cambi[aá] de (?:tema|canci[oó]n))$", "music_control", "what=next"),
        (r"^(?:busc[aá](?:me)?|encontr[aá](?:me)?|abr[ií](?:me)?) (?:el |la |los |las )?(pdf|excel|word|planilla|documento|archivo|presentaci[oó]n|deck|foto|imagen|captura) (?:de(?:l| la)? |que |con )?(.+)$", "find_file", "file"),
        (r"^(?:orden[aá]|limpi[aá]|acomod[aá])(?:me)? (?:el )?escritorio$", "clean_desktop", None),
        (r"^(?:che\s+)?(?:anot[aá] que|tom[aá] nota(?: de)?)\s+(?!la tarea)(?:que\s+|esto:?\s+)?(.+)", "meeting_note", "text"),
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
        # mensajes: "mandale a Juan por wsp que ya salgo" / "escribile a Lara por Slack que…" (destinatario y texto, sin IA)
        (r"^(?:che,?\s+)?(?:mand[aá]le|escrib[ií]le|avis[aá]le|decile)(?: un (?:mensaje|whatsapp|wsp|wpp))? a ([\wáéíóúñ ]{2,30}?)\s+(?:por|en|al)\s+(whatsapp|whats|wsp|wpp|wasap|slack)\s*(?:(?:que|diciendo(?: que)?|:)\s*(.+))?$", "msg_send", "msg"),
        (r"^(?:che,?\s+)?(?:mand[aá]le|escrib[ií]le|mand[aá])\s+un\s+(whatsapp|whats|wsp|wpp|wasap|slack|mensaje por whatsapp|mensaje por slack)\s+a\s+([\wáéíóúñ ]{2,30}?)\s*(?:(?:que|diciendo(?: que)?|:)\s*(.+))?$", "msg_send", "msg2"),
        # "tengo que / hay que / me tengo que acordar de X (el lunes)" -> tarea
        (r"^(?:che,?\s+)?(?!.*\b(?:reuni[oó]n|turno)\b.*\ba las\b)(?:tengo que|hay que|me tengo que acordar de|no me puedo olvidar de)\s+(.+)", "add_task", "text"),
        # (una reunión/turno con hora va al calendario, no a tareas: eso lo decide la IA)
        (r"^(?!.*\b(?:reuni[oó]n|evento|turno|llamada|meet|call)\b.*\ba las\b)(?:agend[aá](?:me)?|anot[aá](?:me)?(?: la tarea)?|nueva tarea|tarea)[:,]?\s+(?:la tarea[:,]?\s+|que\s+)?(.+)", "add_task", "text"),
        # música: sólo si no habla de tareas, fechas, recordatorios ni cosas de trabajo (eso lo decide la IA)
        (r"^(?!.*\b(?:ma[nñ]ana|hoy|lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|tarea|reuni[oó]n|proyecto)\b)(?:cambi[aá]|pas[aá]) a (.+?)(?: en spotify)?$", "music_play", "query"),
        (r"^(?!.*\b(?:recordatorio|alarma|timer|tarea|nota|reuni[oó]n|proyecto|mail|correo|archivo|carpeta|fecha|ma[nñ]ana|hoy)\b)(?:pon[eé]|reproduc[ií]|tir[aá]|met[eé]) (?:m[uú]sica de |a |el tema |la canci[oó]n )?(.+?)(?: en spotify)?$", "music_play", "query"),
        (r"^(?:paus[aá]|par[aá] la m[uú]sica|pausa(?:r)? la m[uú]sica)$", "music_control", "what=pause"),
        (r"^(?:cerr[aá]|cerrame|cerr[aá]me|sal[ií] de|mat[aá])\s+(?:el |la |los |las )?(?:app |programa |aplicaci[oó]n )?([\w .+-]{2,30})$", "close_app", "name"),
        (r"^(?:bloque[aá]|bloquear)(?: la)? (?:compu|pc|computadora|pantalla)$", "lock_pc", None),
        (r"^(?!.*\b(?:mail|correo|archivo|pdf|excel|documento|carpeta|nota|reuni[oó]n|tarea)\b)(?:abr[ií]|abrime|abr[ií]me|ejecut[aá]|inici[aá])\s+(?:el |la |los |las )?(?:app |programa |aplicaci[oó]n )?([\w .+-]{2,30})$", "open_app", "name"),
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
        if "¿día?" in text: return None             # día mal escuchado: que la IA pregunte, no agendar a ciegas
        t = text.strip().strip("¿¡?!.").strip()
        for pat, action, arg in self.FAST:
            m = re.match(pat, t, re.I)
            if not m: continue
            args = {}
            if arg and "=" in arg: k, v = arg.split("="); args[k] = v
            elif arg == "move":
                return {"action": "move_task", "args": {"task": (m.group(1) or m.group(2)).strip(),
                        "date": self._dia(m.group(3)).isoformat()}}
            elif arg == "msg2":
                canal = "slack_send" if "slack" in m.group(1).lower() else "whatsapp_send"
                return {"action": canal, "args": {"to": m.group(2).strip(), "text": (m.group(3) or "").strip(" .")}}
            elif arg == "msg":
                canal = "slack_send" if m.group(2).lower() == "slack" else "whatsapp_send"
                return {"action": canal, "args": {"to": m.group(1).strip(), "text": (m.group(3) or "").strip(" .")}}
            elif action == "find_file":
                return {"action": "find_file", "args": {"query": f"{m.group(1)} {m.group(2)}".strip()}}
            elif action == "timer":
                return {"action": "timer", "args": {"minutes": int(m.group(1)), "label": "Timer"}}
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
        "slack_send": "Preparando el mensaje en Slack", "whatsapp_send": "Preparando el mensaje en WhatsApp", "find_file": "Buscando el archivo", "clean_desktop": "Mirando tu escritorio",
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

    def gemini_key(self):
        return (sv.config().get("gemini_key") or "").strip()

    def handle_audio(self, audio):
        """Voz con Gemini: el audio va directo y vuelve lo que dijiste + qué hacer (sin Whisper ni IA local)."""
        import mate_gemini as G
        self._step("Entendiendo lo que dijiste")
        try:
            r = G.understand_audio(self.gemini_key(), audio, self.system_prompt(), self.history,
                                   sv.config().get("gemini_model"))
        except Exception as e:
            log(f"gemini audio: {e}")
            return None, None                      # la interfaz cae a Whisper local
        heard = (r.get("heard") or "").strip()
        log(f"gemini oyó: {heard!r} -> {r.get('action')} ({r.get('_secs')}s)")
        if not heard or r.get("action") == "none": return "", None
        quick = self.charla(heard) or None
        if quick: return heard, quick
        fast = self.fast_route(heard)              # los atajos instantáneos siguen ganando
        return heard, self.handle(heard, routed=fast or r)

    SEGURAS_EN_LOTE = {"move_task", "task_status", "add_task", "delete_task", "remember", "reminder", "cal_add"}

    def _run_many(self, r):
        """Ejecuta varias acciones de un pedido (ej. repartir tareas). Sólo acciones de tareas/agenda:
        nada que mande mensajes sale en lote."""
        hechas, fallas = [], []
        self._step(f"Haciendo {len(r['actions'])} cambios")
        for it in r["actions"][:30]:
            a = it.get("action"); args = self.fix_args(a, it.get("args") or {})
            fn = getattr(self, "do_" + str(a), None)
            if a not in self.SEGURAS_EN_LOTE or not fn:
                fallas.append(f"{a} (no lo hago en lote)"); continue
            try:
                out = fn(args); hechas.append(out)
            except Exception as e:
                fallas.append(str(e)[:60])
        save(self.d); self.ui.refresh_board()
        msg = (r.get("reply") or f"Listo, hice {len(hechas)} cambios").strip()
        if not msg.startswith("✓"): msg = "✓ " + msg
        detalle = "\n".join(h.replace("✓ ", "• ") for h in hechas if h)
        if fallas: detalle += "\n✗ No pude: " + "; ".join(fallas)
        return msg + ("\n" + detalle if detalle else "")

    def handle(self, text, routed=None):
        self.last_text = text
        quick = None if routed else self.charla(text)
        if quick:
            self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": quick}]
            return quick
        r = routed or self.fast_route(text)
        raw = json.dumps(r, ensure_ascii=False) if r else None
        if not r and self.gemini_key():            # pedido escrito: Gemini entiende en ~1 s
            import mate_gemini as G
            self._step("Entendiendo el pedido")
            for intento in (1, 2):                 # si Google está saturado, reintento antes de usar la IA chica
                try:
                    r = G.understand_text(self.gemini_key(), text, self.system_prompt(), self.history,
                                          sv.config().get("gemini_model"))
                    raw = json.dumps(r, ensure_ascii=False); break
                except Exception as e:
                    log(f"gemini texto (intento {intento}): {e}"); r = None
                    if intento == 1: self._step("Google está saturado, reintento"); time.sleep(1.5)
        if not r:   # sin nube: clasificador rápido + IA local chica que sólo elige entre 3 acciones
            self._step("Gemini no responde: lo entiendo acá" if self.gemini_key() else "Entendiendo el pedido")
            r = self.route_local(text)
            if r: raw = json.dumps(r, ensure_ascii=False)
        if not r:
            msgs = [{"role": "system", "content": self.system_prompt(text, compacto=True)}] + self.history[-2:] + \
                   [{"role": "user", "content": text}]
            self._step("Gemini no responde: uso la IA local (~15 s)" if self.gemini_key() else "Entendiendo el pedido")
            try:
                raw = llm(msgs)
            except Exception as e:
                return "✗ No llego a Ollama. ¿Está abierto? (" + str(e)[:60] + ")"
            try: r = json.loads(raw)
            except Exception: r = {"action": "chat", "reply": raw[:300]}
        self.history += [{"role": "user", "content": text}, {"role": "assistant", "content": raw}]
        if isinstance(r.get("actions"), list) and r["actions"]:   # varios cambios de una
            return self._run_many(r)
        a = r.get("action") or "chat"
        if a == "chat" and "reply" in r and not (r.get("reply") or "").strip() and routed is None and self.gemini_key():
            return ""                              # no era un pedido: no hago nada (ni charla de relleno)
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
        proj = self.match_project(a.get("project") or "") if a.get("project") else None
        t = {"id": uuid.uuid4().hex[:8], "text": a["text"], "date": d.isoformat() if d else None, "status": "pendiente"}
        if proj: t["from"] = proj
        self.d["tasks"].append(t)
        self.ui.refresh_board()
        return ar.di("tarea", t=a["text"], f=(f" para {nice_date(d.isoformat())}" if d else "")) + (f" · {proj}" if proj else "")

    # ================= proyectos =================
    def match_project(self, name):
        """Nombre que dijo la IA o el usuario -> el proyecto tal cual lo cargaste (o el texto si no hay match)."""
        name = (name or "").strip()
        names = [p["name"] for p in self.d.get("projects", [])]
        if not name or not names: return name or None
        low = {n.lower(): n for n in names}
        if name.lower() in low: return low[name.lower()]
        hit = next((n for n in names if n.lower() in name.lower() or name.lower() in n.lower()), None)
        if hit: return hit
        m = difflib.get_close_matches(name.lower(), list(low), 1, 0.6)
        return low[m[0]] if m else name

    def do_project_add(self, a):
        """'Tengo un proyecto X que trata de Y' -> lo guarda (o actualiza la descripción si ya existía)."""
        name = (a.get("name") or "").strip()
        desc = (a.get("description") or "").strip()
        if not name: return "¿Cómo se llama el proyecto?"
        ps = self.d.setdefault("projects", [])
        old = next((p for p in ps if p["name"].lower() == name.lower()), None)
        if old:
            if desc: old["description"] = (old.get("description", "") + " " + desc).strip()
            res = f"✓ Actualicé el proyecto {old['name']}"
        else:
            ps.append({"name": name, "description": desc, "active": True})
            res = f"✓ Anoté el proyecto {name}" + (f": {desc[:80]}" if desc else ". ¿De qué trata? Contame y lo sumo")
        self.ui.push(); return res

    def do_project_list(self, a):
        ps = [p for p in self.d.get("projects", []) if p.get("active", True)]
        if not ps: return "Todavía no me contaste tus proyectos. Decime: 'tengo un proyecto X que trata de…'"
        return "Tus proyectos:\n" + "\n".join(f"• {p['name']}" + (f": {p['description'][:90]}" if p.get("description") else "") for p in ps)

    def do_project_delete(self, a):
        n = self.match_project(a.get("name"))
        ps = self.d.get("projects", [])
        p = next((p for p in ps if p["name"] == n), None)
        if not p: return f"No tengo un proyecto '{a.get('name')}'"
        if not self.ui.confirm(f"¿Saco el proyecto {p['name']}?\nLas tareas quedan, sólo deja de usarlo para clasificar."): return "Ok, lo dejo"
        ps.remove(p); self.ui.push(); return f"✓ Saqué {p['name']}"

    def projects_text(self):
        ps = [p for p in self.d.get("projects", []) if p.get("active", True)]
        return "\n".join(f"- {p['name']}: {p.get('description') or '(sin descripción)'}" for p in ps) or "(no cargó proyectos todavía)"

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

    def _brief_con_voz(self):
        """Resumen del primer arranque: en pantalla va completo; en voz, sólo lo importante (antes no decía nada)."""
        txt = self.do_morning_brief()
        try:
            today = dt.date.today().isoformat()
            hoy = self.tasks_in("today")
            atr = sum(1 for t in hoy if t["date"] < today); due = sum(1 for t in hoy if t["date"] == today)
            dicho = [ar.saludo().split("?")[0].replace("¡", "").strip() + "."]
            if atr: dicho.append(f"Tenés {atr} tarea{'s' if atr > 1 else ''} atrasada{'s' if atr > 1 else ''}.")
            dicho.append(f"Para hoy, {due} tarea{'s' if due != 1 else ''}." if due else "Para hoy no tenés tareas.")
            ev = re.search(r"^Calendario: (.+)$", txt, re.M)
            if ev:
                n = ev.group(1).count(",") + 1
                primero = ev.group(1).split(",")[0].strip()
                dicho.append(f"En el calendario tenés {n} evento{'s' if n > 1 else ''}; el primero, {primero}.")
            m = re.search(r"Tenés (\d+) mail", txt)
            if m: dicho.append(f"Y {m.group(1)} mail{'s' if m.group(1) != '1' else ''} sin leer.")
            if not self.ui.in_meeting():
                self.ui.speaker.say(" ".join(dicho))
        except Exception as e:
            log_error("resumen hablado", e)
        return txt

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
    def do_lock_pc(self, a):
        if not _re_bloq(getattr(self, "last_text", "")):   # el modelo a veces confunde "cerrá X" con bloquear
            return "No entendí si querés bloquear la compu. Decime \"bloqueá la compu\""
        sv.lock_pc(); return "✓ Bloqueada"

    def do_close_app(self, a):
        """Cierra una app como si tocaras la X (le deja guardar). Nunca cierra a Mate ni a Windows."""
        name = (a.get("name") or "").lower().strip()
        if not name: return "¿Qué app cierro?"
        if sv.IS_MAC:
            hits = sv.mac.close_app(name)
            return f"✓ Cerré {', '.join(hits)}" if hits else f"No encontré {a.get('name')} abierto"
        import psutil, ctypes, ctypes.wintypes as W
        pids = {p.pid for p in psutil.process_iter(["name"])
                if name.split()[0] in (p.info["name"] or "").lower()
                and (p.info["name"] or "").lower() not in ("explorer.exe", "python.exe", "pythonw.exe", "dwm.exe")}
        if not pids: return f"No encontré {a.get('name')} abierto"
        u = ctypes.windll.user32; hits = []
        def cb(h, _):
            p = W.DWORD(); u.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value in pids and u.IsWindowVisible(h) and u.GetWindowTextLengthW(h): hits.append(h)
            return True
        u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, W.HWND, W.LPARAM)(cb), 0)
        for h in hits: u.PostMessageW(h, 0x0010, 0, 0)   # WM_CLOSE
        return f"✓ Cerré {a.get('name')}" if hits else f"{a.get('name')} está abierto pero sin ventanas para cerrar"
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

    def wa_recent_add(self, name):
        """Guarda a quién le mandaste por WhatsApp (los últimos 12, el más reciente primero)."""
        r = [n for n in self.d.get("wa_recent", []) if n.lower() != name.lower()]
        self.d["wa_recent"] = ([name] + r)[:12]; save(self.d)

    def wa_contacts(self):
        """Recientes (de Mate) + chats de WhatsApp (si está abierto se actualiza la lista guardada)."""
        import mate_control as C
        try:
            chats = C.whatsapp_chats()
            if chats: self.d["wa_chats"] = chats; save(self.d)
        except Exception:
            pass
        recent = self.d.get("wa_recent", [])
        chats = [c for c in self.d.get("wa_chats", []) if c.lower() not in {r.lower() for r in recent}]
        return {"recent": recent, "chats": chats}

    def do_whatsapp_send(self, a):
        """Mensaje (y archivo opcional) por WhatsApp de escritorio. Siempre con confirmación y 5 s para deshacer."""
        import mate_control as C
        to, text = (a.get("to") or "").strip(), (a.get("text") or "").strip()
        if not to: return "¿A quién se lo mando por WhatsApp?"
        f = None
        if a.get("file"):
            hits = C.find_file(a["file"])
            if not hits: return f"✗ No encontré ningún archivo que sea '{a['file']}'"
            f = hits[0]
        ok, msg = C.whatsapp_prepare(to, text, f)
        if not ok: return msg
        resumen = f"Para: {to}\nMensaje: {text or '(sin texto)'}" + (f"\nArchivo: {f.name}" if f else "")
        if not self.ui.confirm(f"¿Se lo mando a {to} por WhatsApp?\n" + resumen + "\nRevisá en WhatsApp que sea el chat correcto.", ok="Mandar"):
            C.whatsapp_discard(); return "Ok, no lo mandé y borré el borrador"
        undo = getattr(self.ui, "undo_window", None)
        if undo and not undo(f"Mandando a {to}…", 5):
            C.whatsapp_discard(); return "Ok, cancelado: no lo mandé"
        res = C.whatsapp_send_now(); self.wa_recent_add(to); return res

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
        sv.open_uri(str(hits[0]))
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
        import mate_control as C, mate_granola as GA, re as _re
        title, body = a.get("nota") or (None, None)      # modo demo: la nota viene armada, sin Granola
        if body: pass
        elif GA.has_key():   # con la API de Granola: se lee de internet, sin abrir la ventana
            try:
                title, body = GA.latest_note(a.get("meeting"))
                log(f"granola api: {'leí ' + repr(title) if body else 'todavía sin nota'}")
            except Exception as e:
                log(f"granola api: {e}")
        if not body and not GA.has_key():
            title, body = C.granola_read_note(a.get("meeting"))
            if not body:
                time.sleep(2); title, body = C.granola_read_note(a.get("meeting"))   # Granola a veces tarda en cambiar de pantalla
            log(f"granola ventana: {'leí ' + repr(title) if body else 'no pude leer la nota'}")
        if not body: return "✗ No pude leer la nota de Granola"
        import mate_servicios, hashlib, unicodedata
        # ¿Esta nota ya la procesé? (reinicios, o la pedís de nuevo): no duplicar
        huella = hashlib.sha1((title + body[:2000]).encode("utf-8", "ignore")).hexdigest()[:16]
        hechas = self.d.setdefault("processed_notes", {})
        forzar = a.get("forzar") or re.search(r"de nuevo|otra vez|nuevamente", getattr(self, "last_text", "") or "", re.I)
        if huella in hechas and not forzar:
            return f"Las tareas de «{title[:40]}» ya las había sacado ({hechas[huella]}). Si querés, pedime que las saque de nuevo."
        me = (mate_servicios.config().get("user_name") or "").strip() or "yo"
        hoy = dt.date.today()
        dias = ", ".join(f"{DIAS[(hoy + dt.timedelta(i)).weekday()]}={(hoy + dt.timedelta(i)).isoformat()}" for i in range(8))
        # Cómo te nombran en las reuniones: tu nombre, con y sin tilde, el diminutivo ("Dami") y los alias de Ajustes.
        # Se compara por palabra entera (antes "Dami" no coincidía y un prefijo podía agarrar a otra persona)
        sin_tilde = lambda s: "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
        nombre = me.split()[0].lower()
        alias = {nombre, sin_tilde(nombre)} | ({nombre[:4], sin_tilde(nombre)[:4]} if len(nombre) >= 5 else set())
        alias |= {x.strip().lower() for x in (mate_servicios.config().get("user_aliases") or "").split(",") if x.strip()}
        rx_yo = re.compile(r"\b(?:" + "|".join(re.escape(x) for x in sorted(alias, key=len, reverse=True)) +
                           r")\b|\b(?:yo|me encargo|i will|i'll)\b", re.I)
        mias = [l.strip(" •◦-") for l in body.split("\n") if rx_yo.search(sin_tilde(l)) or rx_yo.search(l)]
        # Las notas completas (no sólo las líneas que te nombran): así la IA ve de QUÉ tema es cada tarea.
        # Una reunión suele tocar varios proyectos: cada tarea lleva el suyo, no el título de la reunión.
        pista = ("\nLíneas que te nombran (ojo, puede haber otras tareas tuyas):\n" + "\n".join("- " + l for l in mias)) if mias else ""
        prompt = (f"Hoy es {DIAS[hoy.weekday()]} {hoy.isoformat()} ({dias}). Notas de la reunión '{title}':\n\n"
                  f"{body[:20000]}\n{pista}\n\n"
                  f"1. Extraé SOLO las tareas o compromisos de {me} (el usuario). Ignorá las de otras personas.\n"
                  "2. Si una línea tiene varias acciones, separalas.\n"
                  f"3. A cada tarea asignale \"proyecto\". Estos son los proyectos del usuario y de qué trata cada uno:\n"
                  f"{self.projects_text()}\n"
                  "   En las reuniones cada proyecto suele tener su TÍTULO como encabezado y debajo se habla de él: la tarea "
                  "pertenece al proyecto de la sección donde aparece. Elegí el proyecto de la lista que corresponda (usá el "
                  "nombre EXACTO de la lista). Si de verdad no es de ninguno, poné un nombre corto del tema (máx 4 palabras). "
                  "Una reunión puede tener varios proyectos: NUNCA uses el título de la reunión para todas.\n"
                  "4. Fecha SOLO si se menciona un día o plazo.\n"
                  'Respondé SOLO JSON: {"tareas":[{"texto":"verbo en infinitivo + qué","proyecto":"...","fecha":"YYYY-MM-DD o vacío"}]}')
        log(f"tareas: le pido a la IA que saque las tuyas ({len(mias)} líneas te nombran)")
        if a.get("respaldo"):   # demo: si la IA tarda más de 25 s, uso la respuesta ya preparada (la demo no se cuelga)
            res = {}
            th = threading.Thread(target=lambda: res.setdefault("out", ask_long(prompt, "Extraés tareas de reuniones. Respondés solo JSON válido.", max_tokens=1200, temp=0)), daemon=True)
            th.start(); th.join(25)
            out = res.get("out") or json.dumps(a["respaldo"], ensure_ascii=False)
            if "out" not in res: log("demo: la IA tardó, uso las tareas preparadas")
        else:
            out = ask_long(prompt, "Extraés tareas de reuniones. Respondés solo JSON válido.", max_tokens=1200, temp=0)
        log(f"tareas: la IA respondió ({len(out or '')} caracteres)")
        m = _re.search(r"\{.*\}", out, _re.S)
        try: tareas = json.loads(m.group(0))["tareas"] if m else []
        except Exception: tareas = []
        tareas = [t for t in tareas if (t.get("texto") or "").strip()]
        if not tareas: return f"De '{title}' no saqué tareas para vos 🙌"
        # ¿Alguna ya está en tu lista? (parecida a una tarea abierta): viene destildada en la tarjeta
        abiertas = [x["text"].lower() for x in self.d["tasks"] if x["status"] != "hecha"]
        for t in tareas:
            t["proyecto"] = self.match_project(t.get("proyecto") or "") or ""
            t["repetida"] = bool(difflib.get_close_matches(t["texto"].lower(), abiertas, 1, 0.85))
            if parse_date(t.get("fecha")): t["fecha_txt"] = nice_date(t["fecha"])
        n = len(tareas)
        # Tarjeta editable: destildás las que no son tuyas y cambiás el proyecto de cada una
        elegidas = self.ui._modal({"kind": "tareas", "title": f"Tus tareas de «{title[:40]}»",
                                   "text": f"Encontré {n} tarea{'s' if n > 1 else ''} para vos. Destildá las que no son y revisá el proyecto.",
                                   "items": tareas, "projects": [p["name"] for p in self.d.get("projects", [])]})
        if not elegidas:
            hechas[huella] = f"{hoy.isoformat()}, no sumaste ninguna"; save(self.d)
            return "Ok, no sumé nada"
        nuevas = []
        for e in elegidas:
            t = tareas[int(e["i"])]; t["proyecto"] = e.get("proyecto", t.get("proyecto"))
            d = parse_date(t.get("fecha"))
            self.d["tasks"].append({"id": uuid.uuid4().hex[:8], "text": t["texto"], "date": d.isoformat() if d else None,
                                    "status": "pendiente", "from": t["proyecto"] or None, "meeting": title})
            nuevas.append(t)
        hechas[huella] = f"{hoy.isoformat()}, sumaste {len(nuevas)}"
        tareas, n = nuevas, len(nuevas)
        self.ui.refresh_board()
        corto = title if len(title) <= 45 else title[:45].rsplit(" ", 1)[0] + "…"
        por_proy = {}
        for t in tareas: por_proy.setdefault(t.get("proyecto") or "sin proyecto", []).append(t["texto"])
        save(self.d)
        detalle = "\n".join(f"• {p}: {len(v)}" for p, v in por_proy.items())
        return f"✓ Sumé {n} tarea{'s' if n != 1 else ''} de «{corto}»\n{detalle}"

    def demo_meeting(self):
        """Modo demo: simula que terminó una reunión grabada con Granola y corre el flujo completo
        (leer la nota → sacar TUS tareas → asignarlas a cada proyecto → tarjeta para confirmar)."""
        import mate_servicios
        nombre = ((mate_servicios.config().get("user_name") or "").strip() or "vos").split()[0]
        body = (DEMO_DIR / "nota_demo.md").read_text(encoding="utf-8").replace("{nombre}", nombre)
        respaldo = json.loads((DEMO_DIR / "tareas_demo.json").read_text(encoding="utf-8"))
        for t in respaldo["tareas"]:                       # "jueves" -> fecha real
            if t.get("fecha") and not re.match(r"\d{4}-", t["fecha"]): t["fecha"] = self._dia(t["fecha"]).isoformat()
        self.ui.notch("Terminó la reunión: te busco tus tareas en Granola", "think", 10)
        return self.do_meeting_tasks({"nota": ("Seguimiento semanal de proyectos", body), "forzar": True, "respaldo": respaldo})

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
        import mate_control as C
        if not sv.IS_MAC:
            import comtypes; comtypes.CoInitialize()
        on, off_since, mine = 0, None, False
        # Si Mate se reinició mientras Granola grababa, lo retoma (antes un reinicio te dejaba sin tareas)
        rec_prev = self.d.get("granola_rec")
        if rec_prev and time.time() - rec_prev < 4 * 3600:
            mine = True
            self.ui.recording = {"title": "Granola está grabando", "since": rec_prev}
            log("granola: retomo una grabación de antes del reinicio")
        while True:
            time.sleep(8 if LIVIANO else 4)   # chequeo liviano (no toca Granola): mira si tiene el micrófono activo
            try: rec = C.granola_capturing()
            except Exception as e:
                log(f"granola watch: {e}"); time.sleep(10); continue
            if rec:
                on += 1; off_since = None
                if on >= 2 and self.ui.recording:                 # la arrancó Mate: igual sigo su final
                    if not mine: self.d["granola_rec"] = time.time(); save(self.d)
                    mine = True
                elif on == 2:                                     # ~8 s seguidos grabando: es una reunión
                    mine = True
                    self.ui.recording = {"title": "Granola está grabando", "since": time.time() - 8}
                    self.d["granola_rec"] = time.time() - 8; save(self.d)
                    self.ui.notch("Veo que Granola está grabando. Al terminar te busco tus tareas", "", 5)
                    log("granola: empezó a grabar")
            else:
                on = 0
                if self.ui.recording and mine:
                    off_since = off_since or time.time()
                    if time.time() - off_since > 16:      # ~16 s sin micrófono: terminó (no un corte de un segundo)
                        mine = False; off_since = None; self.ui.recording = None
                        self.d.pop("granola_rec", None); save(self.d)
                        log("granola: terminó la grabación")
                        self.ui.notch("Terminó la reunión: te busco tus tareas en Granola", "think", 10)
                        self.ui.background(self._tasks_when_ready)

    def _tasks_when_ready(self):
        """Granola tarda en armar las notas (más en reuniones largas): pruebo enseguida y reintento cada vez más
        espaciado, hasta ~10 minutos, avisando en la isla. Antes se rendía a los ~77 s y te quedabas sin tareas."""
        res = "✗ No pude leer la nota de Granola"
        total = 0
        for espera in (12, 15, 25, 40, 60, 90, 120, 180):
            time.sleep(espera); total += espera
            res = self.do_meeting_tasks({})
            if not res.startswith("✗ No pude leer"): break
            log(f"granola: notas todavía no listas ({total} s), reintento")
            self.ui.notch("Granola todavía está armando la nota… te aviso cuando esté", "think", 8)
        if res.startswith("✗ No pude leer"):
            res = ("✗ Granola no terminó la nota después de 10 minutos. Cuando esté, decime "
                   "«¿qué tareas me quedaron de la última reunión?» y las saco.")
        return res

    def tick(self):
        now = time.time()
        if not getattr(self, "_gw", False):
            self._gw = True; threading.Thread(target=self._granola_watch, daemon=True).start()
        if int(now) % 30 == 0:
            try: self._meeting_watch()
            except Exception as e:
                from mate_audio import log_error; log_error("avisos de reuniones", e)
        due = [r for r in self.d["reminders"] if r["ts"] <= now]
        if due:
            self.d["reminders"] = [r for r in self.d["reminders"] if r["ts"] > now]; save(self.d)
            for r in due: self.ui.alert("🔔 " + r["text"])
        if dt.datetime.now().hour >= 5 and self.d.get("last_brief") != dt.date.today().isoformat():
            self.d["last_brief"] = dt.date.today().isoformat()  # primer arranque del día
            self.ui.background(self._brief_con_voz, speak=False)   # se ve entero y se dice una versión corta
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
        if not DEMO and CFG.get("mail_alerts", True) and now - self.last_mail_check > (180 if LIVIANO else 90) and self.gmail.accounts():
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
        """Avisa de los mails que llegaron desde el último chequeo. Usa la HORA de llegada (guardada en disco),
        así un reinicio de Mate no se 'come' los mails, y avisa aunque ya lo hayas abierto en el celular."""
        try:
            # "category:primary" no anda en todas las cuentas por la API: excluyo las otras pestañas
            new = self.gmail.unread(n=10, q="in:inbox newer_than:1d -category:promotions -category:social "
                                            "-category:updates -category:forums")
            desde = self.d.get("mail_mark") or (time.time() - 15 * 60)   # primera vez: los últimos 15 min
            avisados = set(self.d.get("seen_mails", []))
            fresh = [m for m in new if m["ts"] > desde and m["id"] not in avisados]
            if new: self.d["mail_mark"] = max([desde] + [m["ts"] for m in new])
            self.d["seen_mails"] = list(avisados | {m["id"] for m in fresh})[-300:]
            save(self.d)
            if fresh: log(f"mails: {len(fresh)} nuevos")
            if not fresh: return
            nivel = sv.config().get("mail_nivel", "importantes")     # todos | importantes | nada (lo de Ajustes)
            if nivel == "nada": return
            top = sorted(fresh, key=self.mail_score, reverse=True)
            if nivel == "importantes": top = [m for m in top if self.mail_score(m) >= 2]
            if not top: return
            m = top[0]
            quien = m["from"].split("<")[0].strip().strip('"')
            extra = f" (+{len(top) - 1} más)" if len(top) > 1 else ""
            self.ui.alert(f"✉ {quien}: {m['subject'][:80]}{extra}", True)   # al instante, sin esperar a la IA
        except Exception as e:
            log(f"mails: error {e}")


# ======================= arranque =======================
def _cerrar_copias_viejas():
    """Una sola copia de Mate: si había otra abierta (o trabada), la cierra antes de arrancar.
    Así, volver a abrir Mate (o reinstalarlo) siempre arregla una copia colgada."""
    try:
        import psutil
        me = os.getpid()
        mi_lanzador = int(os.environ.get("MATE_LAUNCHER_PID", "0") or 0)
        for p in psutil.process_iter(["pid", "cmdline"]):
            cmd = " ".join(p.info["cmdline"] or [])
            if p.info["pid"] in (me, mi_lanzador) or "python" not in cmd.lower(): continue
            # primero los lanzadores viejos (si no, al matar su Mate lo relanzarían), después los Mate viejos
            if "mate_lanzador.py" in cmd or "mate.py" in cmd:
                p.kill()
    except Exception:
        pass


if __name__ == "__main__":
    _cerrar_copias_viejas()
    if "--cerrar" in sys.argv: sys.exit(0)        # "Cerrar Mate" del menú Inicio
    import mate_audio; mate_audio.instalar_red_de_seguridad()   # errores y caídas quedan en mate.log / crash.log
    log("Mate arrancó")
    from mate_ui import WebUI
    WebUI(Agent, Speaker, Listener, CFG, WAKE, NAME).start()
