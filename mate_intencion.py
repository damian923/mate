"""Clasificador rápido de intenciones (sin nube, ~50 ms en CPU).

Compara lo que pediste con frases de ejemplo de cada acción (embeddings del modelo multilingüe MiniLM) y
dice qué acción es y con cuánta seguridad. Si está seguro, Mate no tiene que mandarle a la IA local la lista
entera de acciones: sólo le pide los datos de ESA acción (mucho más corto y rápido), o directamente la hace.
Idea de la auditoría: "clasificador por ejemplos + extracción de datos", 70-80% de los pedidos en < 1 s."""
import threading, time
import numpy as np

MODELO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

EJEMPLOS = {
    "add_task": ["tengo que llamar a Raúl el lunes", "che tengo que pagar el alquiler", "me tengo que acordar de mandar el mail a Lara", "agendá llamar al contador el jueves", "anotame comprar yerba", "tengo que mandar el informe el viernes",
                 "nueva tarea revisar el presupuesto", "agregá a mis tareas pagar la luz", "acordame de hacer la propuesta para Juan",
                 "sumá una tarea: preparar la demo", "tengo pendiente llamar al banco mañana"],
    "task_status": ["ya terminé lo del presupuesto", "terminé con lo de Juan", "hecho lo del informe", "terminé lo del contador", "ya hice la propuesta", "marcá como hecha la de DM", "lo del banco está esperando respuesta",
                    "ya llamé al banco", "listo con el informe", "la propuesta está en curso", "completé la tarea de la demo"],
    "move_task": ["pasá la tarea del contador al lunes", "mové lo de DM para el viernes", "reprogramá la propuesta para mañana",
                  "la tarea de Juan pasala para la semana que viene", "cambiá la fecha del informe al jueves"],
    "delete_task": ["borrá la tarea de comprar yerba", "eliminá lo del banco", "sacá la tarea del informe", "ya no hace falta lo de Juan, borralo"],
    "list_tasks": ["qué tengo esta semana", "mostrame mis tareas", "qué tareas tengo pendientes", "cuáles son mis tareas atrasadas"],
    "morning_brief": ["qué tengo para hoy", "cómo viene el día", "resumen del día", "qué hay hoy"],
    "reminder": ["avisame en media hora que apague el horno", "en 10 minutos recordame mirar el horno", "recordame a las 18 llamar a Raúl", "avisame en 20 minutos que saque la ropa", "poneme un recordatorio para las 5",
                 "recordame en una hora tomar la pastilla", "avisame a las 3 que tengo que salir"],
    "timer": ["poné un timer de 10 minutos", "temporizador de 5 minutos", "cronómetro de media hora"],
    "focus": ["modo foco 50 minutos", "quiero concentrarme una hora", "arrancá un pomodoro", "modo concentración"],
    "remember": ["acordate que mi contador se llama Raúl", "mi hermana se llama Sofi", "guardá que mi cumpleaños es el 3 de mayo"],
    "memories": ["qué sabés de mí", "qué te acordás de mí"],
    "cal_events": ["qué reuniones tengo mañana", "qué tengo en el calendario el jueves", "tengo reuniones hoy", "mi agenda del viernes"],
    "cal_add": ["agendame una reunión con Lara el viernes a las 15", "poné en el calendario el dentista el martes a las 10",
                "creá un evento mañana a las 9 con el equipo"],
    "open_app": ["abrí Chrome", "abrime el Excel", "abrí Spotify", "ejecutá el Word", "iniciá Slack"],
    "close_app": ["cerrá el Excel", "cerrá Word", "matá el Chrome", "cerrame Slack", "cerrá Chrome", "cerrame el Spotify", "salí de Excel", "cerrá la app de WhatsApp"],
    "music_play": ["poné Soda Stereo", "poneme algo de Cerati", "quiero escuchar música tranquila", "reproducí Los Piojos", "tirame un tema de Charly"],
    "music_control": ["pausá la música", "siguiente tema", "pasá la canción", "seguí con la música", "pará la música"],
    "volume": ["bajá un poco la música", "subile el volumen a la música", "más bajito", "subí el volumen", "bajá el volumen", "más fuerte", "silenciá"],
    "dolar": ["a cómo está el dólar", "cuánto está el blue", "cotización del dólar hoy"],
    "web_answer": ["cómo va a estar el clima mañana", "va a llover hoy", "quién ganó el partido de anoche", "qué temperatura hace",
                   "cuánto es 15 por 23", "quién es el presidente de Uruguay"],
    "web_open": ["buscá vuelos a Bariloche", "buscame en Google recetas de empanadas", "abrí la página de la AFIP"],
    "mail_check": ["tengo mails nuevos", "revisá mi correo", "me llegó algo al mail", "leeme los mails"],
    "mail_reply": ["respondé el primer mail diciendo que el jueves puedo", "contestale a Juan que sí", "respondé el mail de Lara"],
    "slack_send": ["mandale a Lara por Slack que ya está listo", "escribile a Juan en Slack", "avisale por Slack al equipo que llego tarde"],
    "whatsapp_send": ["mandale a Juan por wsp que ya salgo", "escribile a mamá por whats", "avisale a Lara por WhatsApp que llego en 10", "mandale un whatsapp a mamá que llego tarde", "escribile por WhatsApp a Juan", "mandá por wasap a Lara el archivo"],
    "find_file": ["buscá el pdf del presupuesto", "encontrame el excel de ventas", "abrí el documento de la propuesta"],
    "clean_desktop": ["ordená el escritorio", "limpiá el escritorio"],
    "record_meeting": ["empezá a grabar la reunión", "grabá esta reunión", "arrancá Granola"],
    "stop_recording": ["frená la grabación", "cortá la grabación", "dejá de grabar"],
    "meeting_brief": ["qué hablamos la última vez con Facundo", "de qué hablamos en la reunión con Lara", "machete de la reunión con Cabrini"],
    "meeting_tasks": ["qué tareas me quedaron de la última reunión", "sacá mis tareas de la reunión", "qué me asignaron en la reunión"],
    "meeting_note": ["anotá que le mando el presupuesto a Cabrini", "tomá nota de que hay que revisar el contrato"],
    "meeting_now": ["qué se decidió hasta ahora", "resumime la reunión", "cómo viene la reunión"],
    "project_add": ["tengo un proyecto que se llama DM, es de marketing", "nuevo proyecto: Agente Copilot para DirectTV", "sumá el proyecto Jurisprudencia RAG"],
    "project_list": ["qué proyectos tengo", "mostrame mis proyectos"],
    "habit_log": ["tomé dos vasos de agua", "hice ejercicio hoy", "leí 20 páginas"],
    "habit_add": ["quiero tomar 8 vasos de agua por día", "quiero leer todos los días", "nuevo hábito: meditar"],
    "clipboard": ["resumime lo que copié", "traducí lo que copié", "explicame lo del portapapeles"],
    "lock_pc": ["bloqueá la compu", "bloqueá la pantalla"],
    "chat": ["contame un chiste", "cómo estás", "gracias", "me parece bien", "qué onda", "buenísimo", "jaja", "dale perfecto", "qué opinás de esto"],
}

_lock = threading.Lock()
_modelo = None
_emb = None          # matriz de ejemplos normalizada
_etiquetas = []


def _cargar():
    global _modelo, _emb, _etiquetas
    with _lock:
        if _modelo is not None: return
        from fastembed import TextEmbedding
        m = TextEmbedding(MODELO)
        frases, etiq = [], []
        for accion, ejs in EJEMPLOS.items():
            frases += ejs; etiq += [accion] * len(ejs)
        e = np.array(list(m.embed(frases)), dtype=np.float32)
        _emb = e / np.linalg.norm(e, axis=1, keepdims=True)
        _etiquetas, _modelo = etiq, m


def precalentar():
    """Carga el modelo en segundo plano al arrancar Mate (la 1ª vez baja ~220 MB)."""
    threading.Thread(target=lambda: _cargar(), daemon=True, name="intencion").start()


def clasificar(texto):
    """-> (acción, similitud 0-1, margen contra la 2ª acción distinta). (None, 0, 0) si el modelo no está listo."""
    if _modelo is None: return None, 0.0, 0.0
    v = np.array(list(_modelo.embed([texto]))[0], dtype=np.float32)
    v /= np.linalg.norm(v)
    sims = _emb @ v
    mejor = {}
    for s, a in zip(sims, _etiquetas):
        if s > mejor.get(a, -1): mejor[a] = float(s)
    orden = sorted(mejor.items(), key=lambda x: -x[1])
    (a1, s1), (_, s2) = orden[0], orden[1]
    return a1, s1, s1 - s2


def candidatas(texto, n=3):
    """Las n acciones más probables (para darle a la IA local sólo esas opciones, no las 40)."""
    if _modelo is None: return []
    v = np.array(list(_modelo.embed([texto]))[0], dtype=np.float32); v /= np.linalg.norm(v)
    mejor = {}
    for s, a in zip(_emb @ v, _etiquetas):
        if s > mejor.get(a, -1): mejor[a] = float(s)
    return [a for a, _ in sorted(mejor.items(), key=lambda x: -x[1])[:n]]


def seguro(texto, umbral=0.72, margen=0.06):
    """La acción si el clasificador está seguro; si no, None (y decide la IA con todas las opciones)."""
    a, s, m = clasificar(texto)
    return a if a and s >= umbral and m >= margen else None
