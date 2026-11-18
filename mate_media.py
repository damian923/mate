"""Lo que está sonando en la compu (Spotify, YouTube en Chrome, etc.) y sus controles.

Usa los controles multimedia de Windows (los mismos que aparecen al tocar las teclas de volumen).
"""
import asyncio, threading

APPS = {"spotify": "Spotify", "chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "vlc": "VLC",
        "music": "Música", "zunemusic": "Música", "brave": "Brave"}


class Media:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.mgr = None

    def _run(self, coro, timeout=4):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _session(self):
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as M
        if self.mgr is None:
            self.mgr = await M.request_async()
        return self.mgr.get_current_session()

    async def _now(self):
        s = await self._session()
        if not s: return None
        p = await s.try_get_media_properties_async()
        st = s.get_playback_info().playback_status     # 4 = sonando, 5 = pausa
        app = (s.source_app_user_model_id or "").lower()
        nice = next((v for k, v in APPS.items() if k in app), app.split("!")[0].split(".")[0].capitalize())
        return {"title": p.title or "", "artist": p.artist or "", "app": nice, "playing": int(st) == 4}

    def now(self):
        try: return self._run(self._now())
        except Exception: return None

    async def _cmd(self, what):
        s = await self._session()
        if not s: return False
        f = {"toggle": s.try_toggle_play_pause_async, "next": s.try_skip_next_async, "prev": s.try_skip_previous_async,
             "play": s.try_play_async, "pause": s.try_pause_async}[what]
        return await f()

    def cmd(self, what):
        try: return self._run(self._cmd(what))
        except Exception: return False
