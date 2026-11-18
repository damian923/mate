"""La argentinidad de Mate: frases variadas, saludos materos, feriados y dólar.

Nivel (config "argentinidad"): "alto" (con todo), "normal" (cálido) o "sobrio" (laburo serio).
"""
import datetime as dt, json, random, urllib.request

NIVEL = "normal"

FRASES = {
    "tarea":      [("✓ Agendado: {t}{f}", "✓ De una, anoté {t}{f}", "✓ Joya, {t} queda agendado{f}", "✓ Listo el pollo: {t}{f}"),
                   ("✓ Agendé {t}{f}",)],
    "hecha":      [("🎉 ¡Bien ahí! Terminaste {t}", "🎉 ¡Golazo! {t}, tachado", "🎉 ¡Vamos! Una menos: {t}", "🎉 ¡Crack! {t} ya está"),
                   ("🎉 Hecha: {t}",)],
    "estado":     [("✓ {t} pasó a {s}", "✓ Anotado, {t} está {s}"), ("✓ {t} → {s}",)],
    "recordar":   [("✓ Dale, te aviso a las {h}", "✓ Tranqui, a las {h} te chiflo", "✓ Agendado, a las {h} te pego el grito"),
                   ("✓ Te aviso a las {h}",)],
    "memoria":    [("✓ Me lo guardo: {t}", "✓ Anotado en la libretita: {t}", "✓ Ya me lo acuerdo: {t}"), ("✓ Guardado: {t}",)],
    "foco":       [("✓ {m} minutos de foco. ¡Dale que va, cebá uno y arrancamos!", "✓ Modo foco: {m} minutos. Yo te cuido el mate"),
                   ("✓ Foco de {m} minutos",)],
    "descanso":   [("☕ ¡Bien ahí! Cinco minutos de descanso: cebate uno y estirá las piernas",
                    "☕ ¡Terminaste la ronda! Pausa de 5: agua, mate o una vuelta"), ("☕ Descanso de 5 minutos",)],
    "fin_descanso": [("🧉 Se terminó el recreo. ¿Otra ronda?", "🧉 Volvemos al ruedo. ¿Le damos otra?"), ("Fin del descanso",)],
    "abrir":      [("✓ Ahí te abro {a}", "✓ Dale, abriendo {a}", "✓ {a}, en camino"), ("✓ Abriendo {a}",)],
    "no_entendi": [("Uh, no te entendí. ¿Me lo repetís?", "Perdón, se me escapó. ¿Cómo era?"), ("No entendí, ¿podés repetir?",)],
    "nada_hoy":   [("No tenés nada para hoy. ¡A disfrutar! 🧉", "Día libre de tareas. Cebate uno tranqui 🧉"), ("No hay tareas para hoy",)],
    "error":      [("✗ Uy, algo salió mal: {e}", "✗ Se me trabó: {e}"), ("✗ Error: {e}",)],
}


def di(clave, **kw):
    alto_normal, sobrio = FRASES[clave]
    pool = sobrio if NIVEL == "sobrio" else alto_normal
    return random.choice(pool).format(**kw)


def saludo():
    h = dt.datetime.now().hour
    if NIVEL == "sobrio":
        return "Buen día." if h < 13 else "Buenas tardes." if h < 20 else "Buenas noches."
    if h < 12:  return random.choice(["¡Buen día! ¿Arrancamos con unos mates? 🧉", "¡Buen día! Pava al fuego y arrancamos 🧉"])
    if h < 16:  return random.choice(["¡Buenas! ¿Cómo viene el día?", "¡Buenas tardes! Seguimos en la ronda 🧉"])
    if h < 20:  return random.choice(["¡Buenas tardes! ¿Merienda con bizcochitos? 🥐", "¡Buenas! Hora de la merienda y un mate 🧉"])
    return random.choice(["¡Buenas noches! Cerremos el día tranqui", "¡Buenas noches! No te quedes hasta tarde, eh"])


PERSONA = {
    "alto":   ("Hablás como un argentino de verdad: voseo siempre (vos, tenés, querés), expresiones como "
               "'dale', 'de una', 'joya', 'che', 'bárbaro', 're', 'ni idea'. Cálido, con humor y alguna referencia al mate. "
               "Nunca uses 'tú', 'quieres', 'tienes' ni 'vale'."),
    "normal": ("Hablás en español rioplatense con voseo (vos, tenés, querés), cálido y con buena onda, "
               "sin exagerar los modismos. Nunca uses 'tú', 'quieres' ni 'tienes'."),
    "sobrio": "Hablás en español rioplatense con voseo, claro, breve y profesional, sin chistes.",
}


def persona():
    return PERSONA.get(NIVEL, PERSONA["normal"])


def _json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mate-asistente/1.0"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


_feriados = {}


def feriados(anio=None):
    anio = anio or dt.date.today().year
    if anio not in _feriados:
        try: _feriados[anio] = {f["date"]: f["localName"] for f in _json(f"https://date.nager.at/api/v3/PublicHolidays/{anio}/AR")}
        except Exception: _feriados[anio] = {}
    return _feriados[anio]


def feriados_proximos(dias=7):
    hoy = dt.date.today()
    out = []
    for i in range(dias + 1):
        d = hoy + dt.timedelta(i)
        nombre = feriados(d.year).get(d.isoformat())
        if nombre: out.append((d, nombre))
    return out


def dolar():
    data = _json("https://dolarapi.com/v1/dolares")
    want = {"oficial": "Oficial", "blue": "Blue", "bolsa": "MEP", "tarjeta": "Tarjeta"}
    lines = [f"{want[d['casa']]}: compra ${d['compra']:,.0f} / venta ${d['venta']:,.0f}".replace(",", ".")
             for d in data if d.get("casa") in want]
    return "💵 Dólar hoy\n" + "\n".join(lines)
