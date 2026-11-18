"""Entender la voz como Gemini: le manda el audio del pedido (sólo después de oír "Mate") y en UN paso
devuelve lo que dijiste y qué hacer. Reemplaza a Whisper + la IA local cuando hay clave (mucho más rápido
y entiende el habla natural). Sin clave, Mate sigue 100% local.

La clave se saca gratis en https://aistudio.google.com/apikey y se pega en Más > Ajustes."""
import base64, io, json, time, urllib.request, wave

import numpy as np

DEFAULT_MODEL = "gemini-2.5-flash"
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


def _call(key, model, system, parts, timeout=20):
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1, "maxOutputTokens": 400,
                             "thinkingConfig": {"thinkingBudget": 0}},   # sin "pensar": respuesta inmediata
    }
    req = urllib.request.Request(URL.format(m=model), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    txt = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(txt)


def understand_audio(key, audio, system, history=None, model=None):
    """Audio del pedido -> {"heard", "action", "args", "reply"}."""
    t0 = time.time()
    ctx = "\n".join(f"{m['role']}: {m['content']}" for m in (history or [])[-4:])
    parts = ([{"text": "Conversación reciente:\n" + ctx}] if ctx else []) + \
            [{"inline_data": {"mime_type": "audio/wav", "data": wav_b64(audio)}}]
    out = _call(key, model or DEFAULT_MODEL, system + INSTRUCCIONES_VOZ, parts)
    out["_secs"] = round(time.time() - t0, 1)
    return out


def understand_text(key, text, system, history=None, model=None):
    """Pedido escrito -> {"action", "args", "reply"} (igual que la IA local, pero en ~1 s)."""
    ctx = "\n".join(f"{m['role']}: {m['content']}" for m in (history or [])[-6:])
    parts = ([{"text": "Conversación reciente:\n" + ctx}] if ctx else []) + [{"text": text}]
    return _call(key, model or DEFAULT_MODEL, system, parts)


def check_key(key, model=None):
    """True si la clave anda (para el botón de Ajustes)."""
    try:
        _call(key, model or DEFAULT_MODEL, 'Respondé {"ok": true}', [{"text": "hola"}], timeout=10)
        return True, ""
    except urllib.error.HTTPError as e:
        return False, f"Google respondió {e.code}: {'clave inválida' if e.code in (400, 403) else e.reason}"
    except Exception as e:
        return False, str(e)[:120]
