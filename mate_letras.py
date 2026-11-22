"""Letra sincronizada de lo que está sonando (idea de Nusa). Fuente: LRCLIB (lrclib.net), base libre y gratis,
sin clave. Sólo se manda el nombre de la canción y el artista."""
import json, re, urllib.parse, urllib.request

_cache = {}


def _get(params):
    url = "https://lrclib.net/api/get?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mate desktop assistant (github.com/damian923/mate)"})
    with urllib.request.urlopen(req, timeout=6) as r:
        return json.loads(r.read())


def _search(artist, title):
    url = "https://lrclib.net/api/search?" + urllib.parse.urlencode({"artist_name": artist, "track_name": title})
    req = urllib.request.Request(url, headers={"User-Agent": "Mate desktop assistant (github.com/damian923/mate)"})
    with urllib.request.urlopen(req, timeout=6) as r:
        res = json.loads(r.read())
    return next((x for x in res if x.get("syncedLyrics")), None)


def lyrics(artist, title, dur=None):
    """[(segundo, línea)] o [] si no hay letra sincronizada."""
    title = re.sub(r"\s*[-(\[].*(remaster|versi[oó]n|live|en vivo|mono|stereo|edit).*$", "", title or "", flags=re.I).strip()
    key = (artist.lower(), title.lower())
    if key in _cache: return _cache[key]
    data = None
    try:
        p = {"artist_name": artist, "track_name": title}
        if dur: p["duration"] = int(dur)
        data = _get(p)
    except Exception:
        pass
    if not (data and data.get("syncedLyrics")):
        try: data = _search(artist, title)
        except Exception: data = None
    lines = []
    for m in re.finditer(r"\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)", (data or {}).get("syncedLyrics") or ""):
        lines.append((int(m.group(1)) * 60 + float(m.group(2)), m.group(3).strip()))
    _cache[key] = lines
    return lines


def line_at(lines, pos):
    """La línea que se está cantando en el segundo `pos` ('' entre estrofas)."""
    cur = ""
    for t, txt in lines:
        if t > pos + 0.25: break
        cur = txt
    return cur
