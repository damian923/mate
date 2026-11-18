"""Granola por su API oficial (https://docs.granola.ai): lee las notas de internet, sin abrir la ventana.
Necesita una clave personal de Granola (Ajustes de Granola > Connectors > API keys; plan Business).
Sin clave, Mate usa la ventana de Granola como antes."""
import json, urllib.parse, urllib.request, difflib

import mate_servicios as sv

BASE = "https://public-api.granola.ai/v1"


def key():
    return (sv.config().get("granola_key") or "").strip()


def has_key():
    return bool(key())


def _get(path, params=None, k=None):
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {k or key()}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def latest_note(title_hint=None):
    """(título, texto) de la última nota (o la que se parezca a title_hint). Sólo aparecen notas con resumen listo."""
    notes = _get("/notes", {"page_size": 10}).get("notes", [])
    if not notes: return None, None
    pick = notes[0]
    if title_hint:
        titles = [n.get("title") or "" for n in notes]
        m = [n for n in notes if title_hint.lower() in (n.get("title") or "").lower()] or \
            [notes[titles.index(t)] for t in difflib.get_close_matches(title_hint, titles, 1, 0.5)]
        if not m: return None, None
        pick = m[0]
    note = _get(f"/notes/{pick['id']}")
    text = note.get("summary_text") or note.get("summary_markdown") or ""
    return note.get("title") or pick.get("title") or "Reunión", text


def check(k):
    try:
        _get("/notes", {"page_size": 1}, k=k); return True, ""
    except urllib.error.HTTPError as e:
        return False, {401: "clave inválida", 403: "tu plan de Granola no permite la API"}.get(e.code, f"error {e.code}")
    except Exception as e:
        return False, str(e)[:100]
