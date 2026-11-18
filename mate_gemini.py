"""Entender la voz como Gemini: le manda el audio del pedido (sólo después de oír "Mate") y en UN paso
devuelve lo que dijiste y qué hacer. Reemplaza a Whisper + la IA local cuando hay clave (mucho más rápido
y entiende el habla natural). Sin clave, Mate sigue 100% local.

La clave se saca gratis en https://aistudio.google.com/apikey y se pega en Más > Ajustes."""
import base64, io, json, time, urllib.request, wave

import numpy as np

DEFAULT_MODEL = "gemini-2.5-flash"
_LAST_OK = None   # el último modelo que respondió bien (se prueba primero)
URL = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"

INSTRUCCIONES_VOZ = """
Vas a recibir el AUDIO de lo que el usuario le dijo a Mate (habla en español rioplatense, con muletillas).
1. Transcribí exactamente lo que pidió en "heard" (sin la palabra "Mate" del principio).
2. Si el audio es ruido, una charla entre otras personas o algo que NO es un pedido para Mate,
   devolvé {"heard": "", "action": "none"}.
3. Si es un pedido, elegí la acción como siempre.
Respondé SOLO un JSON: {"heard": "...", "action": "...", "args": {...}, "reply": "..."}"""


def wav_b64(audio, rate=16000):
    """numpy float32 (-1..1) -> WAV 16 bits en base64."""
    pcm = (np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(pcm)
    return base64.b64encode(buf.getvalue()).decode()


def _call(key, model, system, parts, timeout=20, max_out=400):
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1, "maxOutputTokens": max_out,
                             "thinkingConfig": {"thinkingBudget": 0}},   # sin "pensar": respuesta inmediata
    }
    import urllib.error
    def send(b):
        req = urllib.request.Request(URL.format(m=model), data=json.dumps(b).encode(),
                                     headers={"Content-Type": "application/json", "x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    try:
        data = send(body)
    except urllib.error.HTTPError as e:
        if e.code != 400: raise
        body["generationConfig"].pop("thinkingConfig", None)   # modelos que no aceptan ese ajuste
        data = send(body)
    txt = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(txt)


def understand_audio(key, audio, system, history=None, model=None):
    """Audio del pedido -> {"heard", "action", "args", "reply"}."""
    t0 = time.time()
    ctx = "\n".join(f"{m['role']}: {m['content']}" for m in (history or [])[-4:])
    parts = ([{"text": "Conversación reciente:\n" + ctx}] if ctx else []) + \
            [{"inline_data": {"mime_type": "audio/wav", "data": wav_b64(audio)}}]
    out = call_any(key, _models(model), system + INSTRUCCIONES_VOZ, parts)
    out["_secs"] = round(time.time() - t0, 1)
    return out


def understand_text(key, text, system, history=None, model=None):
    """Pedido escrito -> {"action", "args", "reply"} (igual que la IA local, pero en ~1 s)."""
    ctx = "\n".join(f"{m['role']}: {m['content']}" for m in (history or [])[-6:])
    parts = ([{"text": "Conversación reciente:\n" + ctx}] if ctx else []) + [{"text": text}]
    return call_any(key, _models(model), system, parts)


def complete(key, model, system, prompt, max_tokens=600):
    """Texto largo (notas de una reunión) -> respuesta. Devuelve el texto tal cual (puede ser JSON)."""
    out = call_any(key, _models(model), system + '\nRespondé con un JSON {"text": "<tu respuesta>"}.',
                   [{"text": prompt}], max_out=max_tokens + 200)
    t = out.get("text", "")
    return t if isinstance(t, str) else json.dumps(t, ensure_ascii=False)


def _models(model):
    """El ajuste puede ser un modelo o una lista (el mejor primero, después los de respaldo)."""
    if isinstance(model, list): return model
    return [model] if model else [DEFAULT_MODEL]


def best_model(key):
    """Le pregunta a Google qué modelos tiene esta clave y elige el Flash más nuevo y estable
    (Google retira modelos viejos: así Mate no depende de un nombre fijo)."""
    import re
    req = urllib.request.Request("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                                 headers={"x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=10) as r:
        models = json.loads(r.read()).get("models", [])
    names = [m["name"].split("/")[-1] for m in models
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    flash = [n for n in names if re.match(r"^gemini-[\d.]+-flash$", n)] or \
            [n for n in names if "flash" in n and "lite" not in n and "image" not in n and "tts" not in n and "live" not in n] or \
            [n for n in names if n.startswith("gemini")]
    ver = lambda n: [float(x) for x in re.findall(r"\d+(?:\.\d+)?", n)[:1]] or [0]
    orden = lambda l: sorted(l, key=lambda n: (ver(n), "preview" not in n, "latest" in n), reverse=True)
    lite = [n for n in names if re.match(r"^gemini-[\d.]+-flash-lite$", n)]   # respaldo: suelen estar menos saturados
    return orden(flash)[:2] + orden(lite)[:2]


def check_key(key):
    """(ok, mensaje, [modelos]) para el botón de Ajustes: la lista va del mejor al de respaldo.
    Si un modelo está saturado (503/429) prueba el siguiente; la clave igual queda válida."""
    import urllib.error
    try:
        models = best_model(key) or [DEFAULT_MODEL]
    except urllib.error.HTTPError as e:
        return False, f"Google respondió {e.code}: {'clave inválida' if e.code in (400, 403) else e.reason}", None
    except Exception as e:
        return False, str(e)[:120], None
    ultimo = ""
    for m in models:
        try:
            _call(key, m, 'Respondé {"ok": true}', [{"text": "hola"}], timeout=15)
            return True, "", [m] + [x for x in models if x != m]
        except urllib.error.HTTPError as e:
            ultimo = f"{e.code} con {m}"
            if e.code not in (429, 500, 503): break
        except Exception as e:
            ultimo = str(e)[:80]
    if ultimo.startswith(("429", "500", "503")):   # la clave sirve: sólo está saturado ahora
        return True, "saturado", models
    return False, f"Google respondió {ultimo}", None


def call_any(key, models, system, parts, max_out=400):
    """Le pregunta a los modelos EN PARALELO y se queda con la primera respuesta buena: si Google tiene
    uno saturado (tarda o da 503), otro contesta. Como mucho ~12 s; después Mate usa la voz local."""
    global _LAST_OK
    import threading, queue
    models = list(models or [DEFAULT_MODEL])
    if _LAST_OK in models: models.remove(_LAST_OK); models.insert(0, _LAST_OK)
    q = queue.Queue()
    def one(m):
        try: q.put((m, _call(key, m, system, parts, timeout=20, max_out=max_out), None))
        except Exception as e: q.put((m, None, e))
    for m in models[:3]: threading.Thread(target=one, args=(m,), daemon=True).start()
    err, t0 = None, time.time()
    limite = 12.5 if max_out <= 400 else 30     # textos largos (notas de reunión) pueden tardar más
    for _ in range(min(3, len(models))):
        try: m, out, e = q.get(timeout=max(0.5, limite - (time.time() - t0)))
        except queue.Empty: break
        if out is not None:
            _LAST_OK = m; return out
        err = e
    raise err or TimeoutError("Gemini no respondió a tiempo")
