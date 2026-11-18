"""El personaje Mate, estilo neón: el propio mate con cara, borde de luz cian→magenta→naranja,
bombilla dorada, yerba que brilla y una estela de fuego que flota.

Se dibuja con Pillow (antialias + resplandor) y se muestra en un Canvas de tkinter.
Estados: idle, drinking, listening, thinking, speaking, happy, alert, sad, sleeping.
"""
import math, random, time
import tkinter as tk
from PIL import Image, ImageDraw, ImageFilter, ImageTk, ImageChops

S = 4                     # supersampling
FPS = 14
CYAN, MAGENTA, ORANGE = (34, 211, 238), (217, 70, 239), (251, 146, 60)
VIOLET, NIGHT = (91, 33, 182), (15, 12, 40)
GOLD, YERBA, WHITE = (250, 204, 21), (74, 222, 128), (240, 249, 255)


def _hex(c): c = c.lstrip("#"); return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def _lerp(a, b, k): return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


class Renderer:
    def __init__(self, w, h, bg):
        self.w, self.h, self.bg = w, h, _hex(bg)
        W, H = w * S, h * S
        # degradé diagonal para el borde de neón
        g = Image.new("RGB", (W, H))
        px = g.load()
        for y in range(H):
            for x in range(W):
                k = min(1, max(0, ((x / W - 0.25) / 0.45) * 0.6 + (1 - (y / H - 0.15) / 0.7) * 0.4))
                px[x, y] = _lerp(CYAN, MAGENTA, k * 2) if k < 0.5 else _lerp(MAGENTA, ORANGE, (k - 0.5) * 2)
        self.edge_grad = g
        # relleno: violeta arriba, noche abajo
        f = Image.new("RGB", (W, H))
        fd = ImageDraw.Draw(f)
        for y in range(H): fd.line([(0, y), (W, y)], fill=_lerp(VIOLET, NIGHT, y / H))
        self.fill_grad = f
        self.cache, self.zoom = {}, 1

    def frame(self, state, t, blink):
        key = (state, int(t * FPS) if state != "drinking" else min(int(t * FPS), 50), blink)
        if key not in self.cache:
            if len(self.cache) > 600: self.cache.clear()
            self.cache[key] = self._render(state, key[1] / FPS, blink)
        return self.cache[key]

    # ------------------------------------------------------------
    def _render(self, s, t, blink):
        W, H = self.w * S, self.h * S
        u = lambda v: v * S
        iu = lambda v: max(1, int(round(v * S)))
        bob = math.sin(t * 2.4) * 1.2
        if s == "happy": bob = -abs(math.sin(t * 8)) * 3.5
        if s == "sleeping": bob = math.sin(t * 1.3) * 0.6
        cx, cy = 30, 30 + bob         # centro del cuerpo (unidades base)

        base = Image.new("RGB", (W, H), self.bg)
        glow = Image.new("RGB", (W, H), (0, 0, 0))
        gd = ImageDraw.Draw(glow)

        # ---- estela de fuego (detrás) ----
        flame = Image.new("RGB", (W, H), (0, 0, 0))
        fd = ImageDraw.Draw(flame)
        speed = 3.5 if s in ("happy", "alert") else 1.2 if s == "sleeping" else 2.2
        for i in range(3):
            pts = []
            for j in range(12):
                k = j / 11
                x = cx - 8 - k * (16 + i * 3)
                y = cy + 10 + k * 6 - i * 4 + math.sin(t * speed + k * 5 + i) * (2 + k * 3)
                pts.append((u(x), u(y)))
            col = _lerp(ORANGE, MAGENTA, i / 2)
            for j in range(len(pts) - 1):
                wdt = int(u(3.2 - i * 0.6) * (1 - j / 12))
                if wdt > 0: fd.line([pts[j], pts[j + 1]], fill=col, width=wdt)
        base = ImageChops.add(base, flame.filter(ImageFilter.GaussianBlur(u(0.6))))
        glow = ImageChops.add(glow, flame)
        gd = ImageDraw.Draw(glow)

        # ---- forma del mate (máscara) ----
        m = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(m)
        md.ellipse([u(cx - 14), u(cy - 10), u(cx + 14), u(cy + 15)], fill=255)        # panza
        md.polygon([(u(cx - 10), u(cy - 4)), (u(cx - 8), u(cy - 14)),
                    (u(cx + 8), u(cy - 14)), (u(cx + 10), u(cy - 4))], fill=255)       # cuello
        md.ellipse([u(cx - 9), u(cy - 17), u(cx + 9), u(cy - 11)], fill=255)          # boca del mate
        edge = ImageChops.subtract(m.filter(ImageFilter.MaxFilter(u(2) - 1 if u(2) % 2 == 0 else u(2))), m)
        edge = edge.filter(ImageFilter.MaxFilter(3))

        base.paste(self.fill_grad, (0, 0), m)
        # brillo interior suave arriba a la izquierda
        hl = Image.new("L", (W, H), 0)
        ImageDraw.Draw(hl).ellipse([u(cx - 11), u(cy - 8), u(cx - 1), u(cy + 4)], fill=70)
        hl = ImageChops.multiply(hl.filter(ImageFilter.GaussianBlur(u(3))), m)
        base.paste((180, 140, 255), (0, 0), hl)
        base.paste(self.edge_grad, (0, 0), edge)
        glow.paste(self.edge_grad, (0, 0), edge.filter(ImageFilter.MaxFilter(5)))

        # ---- yerba en la boca ----
        d = ImageDraw.Draw(base)
        d.ellipse([u(cx - 7.5), u(cy - 16), u(cx + 7.5), u(cy - 12)], fill=(22, 101, 52))
        d.ellipse([u(cx - 6), u(cy - 15.6), u(cx + 6), u(cy - 13)], fill=YERBA)
        gd.ellipse([u(cx - 7), u(cy - 16), u(cx + 7), u(cy - 12)], fill=(30, 140, 70))

        # ---- bombilla ----
        sip = s == "drinking" and 0.5 < t < 2.7
        if sip:   # la bombilla se dobla hasta la boca: se ceba solo
            pts = [(cx + 3, cy - 14), (cx + 12, cy - 22), (cx + 16, cy - 10), (cx + 6, cy + 5)]
            curve = [self._bez(pts, k / 16) for k in range(17)]
            d.line([(u(x), u(y)) for x, y in curve], fill=GOLD, width=iu(1.4), joint="curve")
            gd.line([(u(x), u(y)) for x, y in curve], fill=(150, 110, 0), width=iu(3))
        else:
            d.line([(u(cx + 3), u(cy - 14)), (u(cx + 10), u(cy - 27))], fill=GOLD, width=iu(1.4))
            d.line([(u(cx + 9), u(cy - 27)), (u(cx + 13), u(cy - 26))], fill=GOLD, width=iu(1.4))
            gd.line([(u(cx + 3), u(cy - 14)), (u(cx + 10), u(cy - 27))], fill=(150, 110, 0), width=iu(3))

        # ---- cara ----
        ex1, ex2, ey = cx - 5.5, cx + 5.5, cy + 1
        eye = WHITE
        closed = s == "sleeping" or sip or (blink and s in ("idle", "speaking", "listening"))
        if closed:
            for ex in (ex1, ex2):
                d.arc([u(ex - 2.6), u(ey - 1.5), u(ex + 2.6), u(ey + 2.5)], 20, 160, fill=eye, width=iu(1.1))
        elif s == "happy" or s == "idle":
            for ex in (ex1, ex2):   # ojitos felices ^ ^
                d.arc([u(ex - 2.6), u(ey - 1.5), u(ex + 2.6), u(ey + 3)], 200, 340, fill=eye, width=iu(1.2))
        elif s == "sad":
            for ex, sgn in ((ex1, -1), (ex2, 1)):
                d.ellipse([u(ex - 1.6), u(ey - 1), u(ex + 1.6), u(ey + 2.4)], fill=eye)
                d.line([(u(ex + sgn * 2.8), u(ey - 2.4)), (u(ex - sgn * 1.2), u(ey - 4))], fill=eye, width=iu(0.9))
            if int(t * 2) % 3 == 1:
                d.ellipse([u(ex1 - 0.8), u(ey + 3), u(ex1 + 0.8), u(ey + 5.5)], fill=CYAN)
        else:
            r = 2.4 if s in ("listening", "alert") else 2
            lx, ly = (0.7, -1) if s == "thinking" else (0, 0)
            for ex in (ex1, ex2):
                d.ellipse([u(ex - r), u(ey - r), u(ex + r), u(ey + r)], fill=eye)
                d.ellipse([u(ex - r * 0.55 + lx), u(ey - r * 0.55 + ly), u(ex + r * 0.55 + lx),
                           u(ey + r * 0.55 + ly)], fill=NIGHT)
                d.ellipse([u(ex + lx), u(ey - 1.2 + ly), u(ex + 0.9 + lx), u(ey - 0.3 + ly)], fill=WHITE)
        for ex in (ex1, ex2): gd.ellipse([u(ex - 3), u(ey - 3), u(ex + 3), u(ey + 3)], fill=(40, 120, 150))
        # cachetes
        for x in (cx - 9.5, cx + 9.5):
            d.ellipse([u(x - 2.2), u(cy + 4), u(x + 2.2), u(cy + 6)], fill=(236, 72, 153))
        # boca
        my = cy + 6
        if s == "speaking":
            o = 0.8 + abs(math.sin(t * 13)) * 2.4
            d.ellipse([u(cx - 2.2), u(my - o / 2), u(cx + 2.2), u(my + o / 2 + 0.6)], fill=WHITE)
        elif s == "happy":
            d.chord([u(cx - 3.5), u(my - 2.5), u(cx + 3.5), u(my + 3)], 0, 180, fill=WHITE)
        elif s == "sad":
            d.arc([u(cx - 2.5), u(my), u(cx + 2.5), u(my + 3.5)], 200, 340, fill=WHITE, width=iu(1))
        elif s == "alert" or sip:
            d.ellipse([u(cx - 1.3), u(my - 1), u(cx + 1.3), u(my + 1.6)], fill=WHITE)
        elif s == "sleeping":
            d.line([(u(cx - 1.5), u(my + 0.5)), (u(cx + 1.5), u(my + 0.5))], fill=WHITE, width=iu(0.9))
        else:
            d.arc([u(cx - 3), u(my - 2.5), u(cx + 3), u(my + 1.8)], 20, 160, fill=WHITE, width=iu(1.1))

        # ---- extras ----
        if sip:
            for i in range(3):
                ph = (t * 1.6 + i / 3) % 1
                x, y = self._bez(pts, 1 - ph)
                d.ellipse([u(x - 0.7), u(y - 0.7), u(x + 0.7), u(y + 0.7)], fill=YERBA)
        if s in ("idle", "drinking", "happy", "sleeping") and not sip:   # vapor
            for i in range(2):
                ph = (t * 0.5 + i * 0.5) % 1
                x0, y0 = cx - 3 + i * 5, cy - 18 - ph * 9
                a = int(150 * (1 - ph))
                gd.line([(u(x0), u(y0)), (u(x0 + 1.5 * math.sin(t * 3 + i)), u(y0 - 3))],
                        fill=(a, a, a + 20), width=iu(1))
        if s == "listening":
            for i in range(3):
                rr = 4 + ((t * 9 + i * 4) % 12)
                c = _lerp(CYAN, (0, 0, 0), ((t * 9 + i * 4) % 12) / 12)
                d.arc([u(cx + 14 - rr), u(cy - rr), u(cx + 14 + rr), u(cy + rr)], -35, 35, fill=c, width=iu(1))
        elif s == "thinking":
            for i in range(3):
                on = int(t * 4) % 4 > i
                c = _lerp(CYAN, MAGENTA, i / 2) if on else (60, 60, 90)
                d.ellipse([u(cx + 14 + i * 4.5), u(8 - i * 2), u(cx + 16.5 + i * 4.5), u(10.5 - i * 2)], fill=c)
                if on: gd.ellipse([u(cx + 13 + i * 4.5), u(7 - i * 2), u(cx + 17.5 + i * 4.5), u(11.5 - i * 2)], fill=c)
        elif s == "alert":
            d.rounded_rectangle([u(cx + 17), u(4), u(cx + 19.5), u(13)], radius=u(1), fill=ORANGE)
            d.ellipse([u(cx + 17), u(15), u(cx + 19.5), u(17.5)], fill=ORANGE)
            gd.rectangle([u(cx + 15), u(2), u(cx + 21.5), u(19)], fill=(150, 80, 20))
        elif s == "sleeping":
            for i in range(2):
                ph = (t * 0.5 + i * 0.5) % 1
                x, y, sz = cx + 13 + ph * 8, 18 - ph * 12, 2.5 + i * 1.2
                d.line([(u(x), u(y)), (u(x + sz), u(y)), (u(x), u(y + sz)), (u(x + sz), u(y + sz))],
                       fill=_lerp(CYAN, MAGENTA, ph), width=iu(0.8))
        if s == "happy":
            for i in range(5):
                a = t * 2.5 + i * 1.256
                x, y = cx + math.cos(a) * 22, cy - 2 + math.sin(a) * 15
                c = [CYAN, MAGENTA, ORANGE, GOLD, YERBA][i]
                self._sparkle(d, gd, u(x), u(y), u(1.6 + 0.6 * math.sin(t * 6 + i)), c)
        else:   # dos chispitas que titilan siempre
            for i, (x, y) in enumerate(((cx - 20, cy - 16), (cx + 19, cy + 11))):
                k = (math.sin(t * 2 + i * 2) + 1) / 2
                self._sparkle(d, gd, u(x), u(y), u(0.6 + k), _lerp(CYAN, MAGENTA, i))

        # ---- resplandor + reducción ----
        g = glow.filter(ImageFilter.GaussianBlur(u(2.2)))
        out = ImageChops.add(base, g)
        out = ImageChops.add(out, glow.filter(ImageFilter.GaussianBlur(u(5))).point(lambda v: v // 2))
        return out.resize((self.w * self.zoom, self.h * self.zoom), Image.LANCZOS)

    @staticmethod
    def _bez(p, k):
        a = [(p[i][0] + (p[i + 1][0] - p[i][0]) * k, p[i][1] + (p[i + 1][1] - p[i][1]) * k) for i in range(3)]
        b = [(a[i][0] + (a[i + 1][0] - a[i][0]) * k, a[i][1] + (a[i + 1][1] - a[i][1]) * k) for i in range(2)]
        return b[0][0] + (b[1][0] - b[0][0]) * k, b[0][1] + (b[1][1] - b[0][1]) * k

    @staticmethod
    def _sparkle(d, gd, x, y, r, c):
        d.polygon([(x, y - r * 2), (x + r * 0.5, y - r * 0.5), (x + r * 2, y), (x + r * 0.5, y + r * 0.5),
                   (x, y + r * 2), (x - r * 0.5, y + r * 0.5), (x - r * 2, y), (x - r * 0.5, y - r * 0.5)], fill=c)
        gd.ellipse([x - r * 2, y - r * 2, x + r * 2, y + r * 2], fill=tuple(v // 2 for v in c))


class Mate(tk.Canvas):
    W, H = 64, 56

    def __init__(self, master, bg):
        super().__init__(master, width=self.W, height=self.H, bg=bg, highlightthickness=0)
        self.r = Renderer(self.W, self.H, bg)
        self.state, self.state_since, self.hold = "idle", time.time(), None
        self.next_blink = time.time() + 3
        self.next_sip = time.time() + random.uniform(20, 40)
        self.img = None
        self.item = self.create_image(0, 0, anchor="nw")
        self._tick()

    def set(self, state, secs=None):
        """Cambia de estado; con secs vuelve solo a 'idle'."""
        if state != self.state:
            self.state, self.state_since = state, time.time()
        self.hold = time.time() + secs if secs else None

    def _tick(self):
        now = time.time()
        if self.hold and now > self.hold:
            self.hold = None; self.state, self.state_since = "idle", now
        if self.state == "idle" and now > self.next_sip:
            self.set("drinking", 3.4); self.next_sip = now + random.uniform(35, 70)
        blink = self.next_blink < now < self.next_blink + 0.14
        if now > self.next_blink + 0.14: self.next_blink = now + random.uniform(2.5, 5)
        t = now - self.state_since
        if self.state not in ("drinking",): t = t % 6   # ciclos de 6 s → frames cacheables
        self.img = ImageTk.PhotoImage(self.r.frame(self.state, t, blink))
        self.itemconfig(self.item, image=self.img)
        self.after(int(1000 / FPS), self._tick)


if __name__ == "__main__":  # demo: recorre todos los estados
    root = tk.Tk(); root.configure(bg="#0d0b1a"); root.title("Mate")
    m = Mate(root, "#0d0b1a"); m.pack(padx=30, pady=20)
    lab = tk.Label(root, bg="#0d0b1a", fg="white"); lab.pack(pady=(0, 10))
    states = ["idle", "drinking", "listening", "thinking", "speaking", "happy", "alert", "sad", "sleeping"]
    def nxt(i=0):
        st = states[i % len(states)]; m.set(st); lab.config(text=st); root.after(3600, nxt, i + 1)
    nxt(); root.mainloop()
