"""Audio de Mate.

Escucha:  Vosk (liviano, siempre prendido) detecta un posible "Mate…" o dos aplausos.
          Whisper (más preciso) confirma y transcribe el pedido. Así no se activa solo
          y entiende bien lo que decís.
Habla:    Piper, voces neuronales en español que corren en tu compu (se eligen en Ajustes).
"""
import json, os, queue, re, threading, time, wave
from pathlib import Path

import numpy as np

DATA_DIR = Path.home() / ".mate"
VOICES_DIR = DATA_DIR / "voces"
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def log(*a):
    """Registro de lo que escucha Mate (~/.mate/mate.log), útil para ajustar la voz."""
    try:
        with open(DATA_DIR / "mate.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a) + chr(10))
    except Exception:
        pass

VOICE_NAMES = {   # archivo -> nombre lindo para Ajustes
    "es_AR-daniela-high": "Daniela (Argentina)",
    "es_MX-claude-high": "Claude (México)",
    "es_MX-ald-medium": "Aldo (México)",
    "es_ES-davefx-medium": "Dave (España)",
    "es_ES-sharvard-medium": "Sharvard (España)",
}


# Voces de Microsoft (necesitan internet; muy naturales). La única masculina argentina disponible es Tomás.
VOICES_ONLINE = {
    "edge:es-AR-TomasNeural": "Tomás (Argentina, hombre · online)",
    "edge:es-AR-ElenaNeural": "Elena (Argentina, mujer · online)",
}


def voices():
    online = [{"id": k, "name": v} for k, v in VOICES_ONLINE.items()]
    return online + [{"id": p.stem, "name": VOICE_NAMES.get(p.stem, p.stem)} for p in sorted(VOICES_DIR.glob("*.onnx"))]


# =============================== VOZ ===============================
class Speaker:
    def __init__(self, voice=None, speed=1.0):
        self.q, self.speaking = queue.Queue(), False
        self.voice_id, self.speed, self._voice = voice, speed, None
        threading.Thread(target=self._run, daemon=True).start()

    def set_voice(self, voice_id):
        self.voice_id, self._voice = voice_id, None

    def _load(self):
        if self._voice is not None: return self._voice
        from piper import PiperVoice
        ids = [p.stem for p in VOICES_DIR.glob("*.onnx")]   # voces locales (respaldo si la online no anda)
        local = self.voice_id if self.voice_id in ids else None
        vid = local or ("es_MX-ald-medium" if str(self.voice_id).startswith("edge:") and "es_MX-ald-medium" in ids else None) \
              or ("es_AR-daniela-high" if "es_AR-daniela-high" in ids else (sorted(ids) or [None])[0])
        if not vid: return None
        self._voice = PiperVoice.load(str(VOICES_DIR / f"{vid}.onnx"))
        return self._voice

    def _synth_online(self, text):
        """Voz de Microsoft (edge-tts): manda el texto a Microsoft y vuelve el audio. ~1-2 s."""
        import asyncio, edge_tts, miniaudio
        voz = self.voice_id.split(":", 1)[1]
        rate = f"{round((self.speed - 1) * 100):+d}%"
        async def get():
            buf = b""
            async for ch in edge_tts.Communicate(text, voz, rate=rate).stream():
                if ch["type"] == "audio": buf += ch["data"]
            return buf
        mp3 = asyncio.run(get())
        d = miniaudio.decode(mp3, nchannels=1, output_format=miniaudio.SampleFormat.SIGNED16)
        return np.frombuffer(d.samples, dtype=np.int16), d.sample_rate

    def _synth(self, text):
        if str(self.voice_id).startswith("edge:"):
            try: return self._synth_online(text)
            except Exception as e: log(f"voz online no disponible ({type(e).__name__}): uso la local")
        from piper import SynthesisConfig
        v = self._load()
        chunks = [c.audio_int16_array for c in v.synthesize(text, syn_config=SynthesisConfig(length_scale=1 / self.speed))]
        return np.concatenate(chunks), v.config.sample_rate

    def _run(self):
        import sounddevice as sd
        try: self._load()
        except Exception: pass
        while True:
            text = self.q.get()
            try:
                audio, sr = self._synth(text)
                self.speaking = True
                sd.play(audio, sr); sd.wait()
            except Exception:
                self._fallback(text)
            time.sleep(0.25); self.speaking = False

    def _fallback(self, text):
        try:
            import pythoncom, pyttsx3
            pythoncom.CoInitialize()
            e = pyttsx3.init(); self.speaking = True; e.say(text); e.runAndWait()
        except Exception:
            pass

    def say(self, text):
        clean = re.sub(r"[^\w\s.,;:¡!¿?%/()'-]", "", text).strip()
        if clean: self.q.put(clean[:500])

    def sample(self, voice_id):
        self.set_voice(voice_id)
        self.say("¡Hola! Soy Mate. Así sueno con esta voz. ¿Te cebo uno?")


# =============================== ESCUCHA ===============================
class Listener:
    """- "Mate, <pedido>"   -> ejecuta el pedido
       - "Mate" sola        -> escucha el próximo pedido
       - dos aplausos       -> escucha el próximo pedido"""
    RATE, BLOCK = 16000, 1600   # bloques de 100 ms

    def __init__(self, wake, on_command, on_listening, speaker, clap_threshold=0.55,
                 whisper_model="base", vocab="", claps=True):
        self.wake, self.on_command, self.on_listening = wake.lower(), on_command, on_listening
        self.speaker, self.thr, self.claps_on = speaker, clap_threshold, claps
        self.whisper_name, self.vocab = whisper_model, vocab
        self.whisper = None
        self.listen_until, self.claps, self.quiet = 0, [], 0.01
        self.utter = []            # audio de la frase actual (para Whisper)
        self.capture = None        # grabación del pedido después de activarse
        self.started = 0
        self.on_heard = None       # callback para mostrar lo que entendió (opcional)
        self.on_wake_hint = None   # callback apenas se oye "Mate" (feedback inmediato)
        self.in_meeting = lambda: False   # la interfaz lo reemplaza: en reunión no se activa con aplausos
        self.on_audio = None   # con Gemini: recibe (audio, por_palabra) y devuelve None si hay que usar Whisper
        self.on_pause = None   # callback cuando hacés una pausa mientras hablás (feedback "te sigo escuchando")
        self.pausa = 1.5       # segundos de silencio que cuentan como "terminé de hablar"

    # ---------------- arranque ----------------
    def start(self):
        try:
            import vosk, sounddevice as sd
        except ImportError as e:
            return f"Voz desactivada (falta {e.name})"
        mdir = DATA_DIR / "vosk-es"
        if not mdir.exists():
            return "Voz desactivada (falta el modelo de voz)"
        vosk.SetLogLevel(-1)
        self.rec = vosk.KaldiRecognizer(vosk.Model(str(mdir)), self.RATE)
        threading.Thread(target=self._load_whisper, daemon=True).start()
        q = queue.Queue()

        def loop():
            with sd.RawInputStream(samplerate=self.RATE, blocksize=self.BLOCK, dtype="int16", channels=1,
                                   callback=lambda d, *_: q.put(bytes(d))):
                while True:
                    self.feed(q.get())
        self.started = time.time()
        threading.Thread(target=loop, daemon=True).start()
        return None

    def feed(self, data):
        """Procesa 100 ms de audio (int16). Lo usa el micrófono y también las pruebas."""
        # El reconocedor de Vosk sólo se toca desde este hilo (el del micrófono): si otro hilo lo
        # reinicia mientras se usa, la librería se cae (y se cerraba Mate). Los otros hilos sólo piden el reset.
        if getattr(self, "_reset_req", False):
            self._reset_req = False; self.rec.Reset(); self._hinted = False
        if self.speaker.speaking:          # no escucharse a sí mismo
            self.rec.Reset(); self.utter = []; return
        s = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768
        self.pre = (getattr(self, "pre", []) + [s])[-7:]     # últimos 0,7 s, para no comerse el principio
        if self.capture is not None:
            self._capture_step(s); return
        self.utter.append(s)
        if len(self.utter) > 120: self.utter = self.utter[-120:]   # máx 12 s
        if self.claps_on and not self.in_meeting(): self._clap(s)   # en reunión, risas y golpes no lo activan
        if self.rec.AcceptWaveform(data):
            text = json.loads(self.rec.Result()).get("text", "")
            audio = np.concatenate(self.utter) if self.utter else None
            self.utter = []; self._hinted = False
            if text: log("vosk:", text); self._candidate(text, audio)
        elif not getattr(self, "_hinted", False):
            # Apenas se oye "Mate" (antes de que termines la frase) la isla ya reacciona: se siente instantáneo
            part = json.loads(self.rec.PartialResult()).get("partial", "")
            if part and self._has_wake(part):
                self._hinted = True
                if self.on_wake_hint:
                    try: self.on_wake_hint()
                    except Exception: pass
                # Apenas oye "Mate" pasa a GRABAR el pedido entero (con lo dicho hasta ahora). Antes Vosk cortaba
                # la frase en la primera pausita (~0,5 s) y se perdía el resto: ahora espera a que de verdad termines.
                self.capture = {"buf": list(self.utter[-60:]), "speech": True, "silence": 0,   # (utter ya trae lo de antes)
                                "t0": time.time(), "follow": False, "wake": True}
                self.utter = []; self._reset_req = True

    def _load_whisper(self):
        # Parakeet v3 (NVIDIA): ~10 veces más rápido que Whisper small en procesador y con menos errores
        # en español (medido en esta compu: 0,3 s contra 3,2 s por frase). Whisper queda de respaldo.
        self.parakeet = None
        try:
            import onnx_asr
            self.parakeet = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v3", quantization="int8")
            log("parakeet listo")
        except Exception as e:
            log("parakeet no disponible:", e)
        if self.parakeet is not None or self.whisper_name == "none":
            return   # Whisper sólo de respaldo si Parakeet no anda: así no ocupa ~500 MB de memoria al divino botón
        try:
            from faster_whisper import WhisperModel
            hilos = max(2, min(4, (os.cpu_count() or 4) - 1))
            self.whisper = WhisperModel(self.whisper_name, device="cpu", compute_type="int8", cpu_threads=hilos)
            log("whisper listo:", self.whisper_name)
        except Exception as e:
            log("whisper ERROR:", e); self.whisper = None

    def transcribe(self, audio):
        """Whisper con pistas de vocabulario. Devuelve '' si es ruido o no está seguro."""
        if audio is None or len(audio) < self.RATE * 0.4: return None
        audio = self._compact(audio)
        if getattr(self, "parakeet", None) is not None:
            try:
                return self._fix_names(str(self.parakeet.recognize(audio.astype(np.float32))).strip())
            except Exception as e:
                log("parakeet error:", e)
        if self.whisper is None:
            return None
        prompt = f"{self.wake.capitalize()}, agendá, recordame, poné música, Spotify, Granola, WhatsApp, tareas, mails. {self.vocab}"
        segs, info = self.whisper.transcribe(audio, language="es", beam_size=1, without_timestamps=True, initial_prompt=prompt,
                                             vad_filter=False, condition_on_previous_text=False)
        segs = list(segs)
        if not segs: return ""
        if np.mean([s.no_speech_prob for s in segs]) > 0.6 or np.mean([s.avg_logprob for s in segs]) < -1.0:
            return ""
        return " ".join(s.text for s in segs).strip()

    def _compact(self, audio, keep=0.3):
        """Acorta los silencios largos (pausas al hablar) a 0,3 s: el reconocedor entiende mejor la frase seguida."""
        win = int(self.RATE * 0.05)
        n = len(audio) // win
        if n < 4: return audio
        frames = audio[:n * win].reshape(n, win)
        rms = np.sqrt((frames ** 2).mean(axis=1))
        thr = max(0.008, float(np.percentile(rms, 30)) * 1.5)
        out, quiet, maxq = [], 0, int(keep / 0.05)
        for f, r in zip(frames, rms):
            quiet = quiet + 1 if r < thr else 0
            if quiet <= maxq: out.append(f)
        return np.concatenate(out) if out else audio

    def _fix_names(self, text):
        """Parakeet no acepta una lista de nombres como Whisper: corrijo después las palabras parecidas
        a nombres que Mate conoce (de tu memoria) y a palabras de Mate ("Cabini" -> "Cabrini")."""
        import difflib
        known = {w.strip(".,").lower(): w.strip(".,") for w in
                 (self.vocab + " Mate Spotify Granola Slack WhatsApp Gmail yerba agendá recordame poné").split() if len(w) > 3}
        out = []
        for w in text.split():
            core = w.strip(".,;:!?¿¡").lower()
            if len(core) > 3 and core not in known:
                m = difflib.get_close_matches(core, list(known), 1, 0.8)
                if m: w = w.lower().replace(core, known[m[0]]) if w.islower() else w.replace(w.strip(".,;:!?¿¡"), known[m[0]])
            out.append(w)
        return " ".join(out)

    # ---------------- palabra clave ----------------
    def _wake_rx(self):
        w = re.escape(self.wake).replace("e", "[eé]")   # mate, maté, mates, che mate…
        return rf"\b{w}s?\b"

    def _has_wake(self, t):
        return re.search(self._wake_rx(), t.lower()) is not None

    def _after_wake(self, t):
        m = re.search(self._wake_rx() + r"[\s,.:!¡¿?]*(.*)", t, re.I)
        return m.group(1).strip(" .,!?¿¡") if m else ""

    def _candidate(self, vosk_text, audio):
        if not self._has_wake(vosk_text): return
        threading.Thread(target=self._confirm, args=(vosk_text, audio), daemon=True).start()

    def _confirm(self, vosk_text, audio):
        # Con Gemini: si dijo "Mate" + algo más, el audio entero va a Gemini (entiende y decide si era para Mate)
        after = self._after_wake(vosk_text)
        if self.on_audio and audio is not None and len(after.split()) >= 2:
            res = self.on_audio(audio, True)
            if res is not None: return                # Gemini lo resolvió (o decidió que no era para Mate)
        text = self.transcribe(audio)
        log("whisper (confirmar):", repr(text))
        if text is None:                          # Whisper todavía cargando: uso Vosk
            text = vosk_text
        if not text or not self._has_wake(text):  # falsa alarma: no dijo "Mate"
            return
        # "Mate" tiene que estar al principio ("Mate, poné…" / "Che Mate…"): si aparece en el medio
        # de una charla ("se mate", "yo mate el…") no es para él
        if not re.search(r"^\W*(?:\w+\W+){0,2}?" + self._wake_rx(), text.lower()):
            log("ignorado: 'mate' en el medio de la frase"); return
        cmd = self._after_wake(text)
        if len(cmd.split()) >= 2: self._deliver(cmd)
        else: self.activate()

    # ---------------- aplausos ----------------
    def _clap(self, s):
        now = time.time()
        rms, peak = float(np.sqrt((s ** 2).mean())), float(np.abs(s).max())
        if now - self.started < 4:
            self.quiet = 0.9 * self.quiet + 0.1 * rms; return
        # aplauso = golpe muy fuerte y corto sobre un ambiente tranquilo
        if peak > self.thr and rms < peak * 0.3 and peak > self.quiet * 12:
            if not self.claps or now - self.claps[-1] > 0.15:
                self.claps = [c for c in self.claps if now - c < 1.0] + [now]
                if len(self.claps) >= 2 and 0.18 < self.claps[-1] - self.claps[-2] < 0.75:
                    self.claps = []; self.activate()
        else:
            self.quiet = 0.97 * self.quiet + 0.03 * rms

    # ---------------- grabar el pedido ----------------
    def activate(self, follow=False):
        """Empieza a grabar el pedido YA (sin hablar antes: si Mate habla, se pisa con lo que decís).
        follow=True: modo conversación, después de que Mate respondió."""
        log("activado (siguiendo la charla)" if follow else "activado (escuchando pedido)")
        self._reset_req = True; self.utter = []   # el reset lo hace el hilo del micrófono (ver feed)
        self.capture = {"buf": list(getattr(self, "pre", [])[-3:]), "speech": False, "silence": 0,
                        "t0": time.time(), "follow": follow}
        try: self.on_listening(follow)
        except TypeError: self.on_listening()

    def _capture_step(self, s):
        c = self.capture
        rms = float(np.sqrt((s ** 2).mean()))
        loud = rms > max(0.012, self.quiet * 3)
        if loud and not c["speech"]:
            c["buf"] = list(self.pre[:-1])                    # suma lo que vino justo antes
        if loud: c["speech"], c["silence"] = True, 0
        elif c["speech"]: c["silence"] += 1
        if c["speech"]: c["buf"].append(s)
        waited = time.time() - c["t0"]
        wait_start = 5 if c.get("follow") else 8           # cuánto espera a que empieces a hablar
        # Fin del pedido: ~1,5 s de silencio (antes 0,8 s cortaba en las pausas normales al pensar qué decir)
        pausa = int(self.pausa * 10)
        if (c["speech"] and c["silence"] >= pausa) or waited > 30 or (not c["speech"] and waited > wait_start):
            audio = np.concatenate(c["buf"]) if c["speech"] and c["buf"] else None
            follow, wake = c.get("follow"), c.get("wake")
            self.capture = None; self.rec.Reset(); self.utter = []
            threading.Thread(target=self._finish_capture, args=(audio, follow, wake), daemon=True).start()
        elif c["speech"] and c["silence"] == 6 and self.on_pause:
            try: self.on_pause()                               # "te sigo escuchando…": sabés que no te cortó
            except Exception: pass

    MULETILLAS = {"ah", "eh", "em", "mm", "mmm", "no", "sí", "si", "este", "bueno", "o", "y", "a", "la", "el",
                  "ok", "okay", "uh", "ay", "che", "gracias", "chau", "nada"}

    def _finish_capture(self, audio, follow=False, wake=False):
        if audio is None:                                  # no dijiste nada: vuelve tranquilo
            log("sin voz", "(fin de la charla)" if follow else "")
            if self.on_heard and not follow: self.on_heard(None)
            return
        if self.on_audio:                                 # Gemini entiende el audio directo
            res = self.on_audio(audio, bool(wake))        # (si empezó por "Mate", Gemini decide si era para él)
            if res is not None: return
        t0 = time.time()
        text = self.transcribe(audio)
        log("voz local (pedido):", repr(text), "dur", round(len(audio) / self.RATE, 1), f"tardó {time.time() - t0:.1f}s")
        if wake:   # empezó porque se oyó "Mate": tiene que estar al principio, si no era charla de otros
            if not text or not re.search(r"^\W*(?:\w+\W+){0,2}?" + self._wake_rx(), text.lower()):
                log("ignorado: no empezaba con 'Mate'"); return
        if text and self._has_wake(text): text = self._after_wake(text) or text
        words = re.sub(r"[^\w ]", "", (text or "").lower()).split()
        real = [w for w in words if w not in self.MULETILLAS]
        if text and (len(words) >= 2 and real or len(real) >= 1 and len(real[0]) > 3):
            self._deliver(text)
        elif self.on_heard and not follow:
            self.on_heard(None)   # no entendió nada: no ejecuta ruido

    def _deliver(self, cmd):
        # el mismo pedido dos veces seguidas (eco del modo charla) no se procesa de nuevo
        prev, t = getattr(self, "_last_cmd", ("", 0))
        if cmd.strip().lower() == prev and time.time() - t < 15:
            log("repetido, lo ignoro:", cmd); return
        self._last_cmd = (cmd.strip().lower(), time.time())
        log("PEDIDO:", cmd)
        self.on_command(cmd)
