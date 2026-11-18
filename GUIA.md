# Mate 🧉 – guía rápida

## Instalar
Abrí **PowerShell** (tecla Windows, escribí "PowerShell", Enter), pegá esta línea y apretá Enter:

    irm https://raw.githubusercontent.com/damian923/mate/main/instalar.ps1 | iex

**En Mac:** abrí **Terminal** (Cmd+Espacio, escribí "Terminal", Enter), pegá esta línea y apretá Enter:

    curl -fsSL https://raw.githubusercontent.com/damian923/mate/main/instalar.sh | bash

Si no tenés Homebrew te pide la contraseña de la Mac para instalarlo. La primera vez que Mate arranca, macOS pide
permisos de **Micrófono**, **Accesibilidad** y **Automatización**: aceptá los tres (Ajustes del Sistema → Privacidad y seguridad).

Necesitás Windows 10/11 o macOS 12+, unos 8 GB de RAM y ~6 GB libres. Si ya tenés la carpeta de Mate, también sirve doble click en **INSTALAR.bat**.
Instala Python, Ollama, los dos modelos de IA,
las librerías, el reconocimiento de voz y las voces, y deja a Mate **arrancando solo al prender la compu**.
Para abrirlo a mano: `pythonw mate.py`. Tus datos y la configuración están en `C:\Users\<vos>\.mate\`.

## Cómo se usa
- **La isla** (arriba al centro): Mate en reposo. Se estira para mostrarte lo que pasa: confirmaciones, música, foco,
  grabación de una reunión o un aviso. Pasá el mouse o hacé click para abrir el panel. **Esc** lo cierra.
- **Hablarle**: decí **"Mate, …"** o **aplaudí dos veces** y hablá cuando aparece "Te escucho".
  Después de responderte **sigue escuchando unos segundos**: podés contestarle sin repetir "Mate". "Gracias" o "chau" cortan.
- **Escribirle**: en el panel, en la barra de abajo a la izquierda.
- **Confirmaciones**: lo que se manda o se borra aparece como un recibo (Para / Mensaje / Archivo): **Enter** confirma, **Esc** cancela.
  Al mandar un Slack o un mail tenés **5 segundos para deshacer**.

## El panel
- **Hoy**: tu próxima reunión, clima, dólar, feriado, tareas del día y hábitos. Arriba, la música que está sonando.
- **Semana**: tus reuniones de la semana y el tablero de tareas (Pendiente / En curso / Esperando / Hecha). Arrastrá entre columnas.
- **Hábitos**, **Correo** y en **Más**: todas las tareas, enfoque, Mi PC, memoria y ajustes.
- **Ajustes**: cuentas de Google, personalidad (Sobrio / Normal / Con todo), vestuario de Mate, tema (automático, oscuro o claro), voz y avisos.

## Ejemplos
- Tareas: "agendá llamar al contador el jueves" · "lo de la propuesta está esperando a Juan" · "terminé lo del contador" · "¿qué tengo hoy?"
- Recordatorios: "recordame a las 18 llamar a Raúl" · "timer de 10 minutos" · "modo foco 50 minutos"
- Memoria: "acordate que mi contador se llama Raúl" · "¿qué sabés de mí?"
- Música: "poné Cerati" · "pausa" · "siguiente" · "subí el volumen"
- Apps: "abrí WhatsApp" · "buscá el deck del reporte semanal" · "ordená el escritorio"
- Internet: "¿cómo va a estar el clima mañana?" · "¿a cómo está el dólar?" · "buscá vuelos a Bariloche"
- Correo y calendario: "¿tengo mails?" · "respondé el 1 diciendo que el jueves puedo" · "¿qué tengo el jueves?"
- Slack: "mandale a Lara por Slack el excel de ventas diciendo que ya está listo"
- Archivos: soltá un archivo sobre la isla para analizarlo, ordenarlo, convertirlo, mandarlo por Slack o mandarlo a la papelera.

## Reuniones (con Google Calendar y Granola)
- **10 min antes** Mate prepara un **machete** con lo que se habló la última vez en esa reunión (lo encontrás en el panel).
- **5 min antes** te avisa. **Cuando empieza** te pregunta si la graba con Granola (Enter = sí).
- Durante la reunión: "**anotá que** le mando el presupuesto a Cabrini" · "**¿qué se decidió hasta ahora?**"
- "**Frená la grabación**": Granola genera las notas y Mate te propone las tareas que te asignaron.
- Cuando quieras: "¿qué hablamos la última vez con Facundo?" · "¿qué tareas me quedaron de la reunión con Lara?"

## Cuentas de Google (Gmail + Calendar)
En **Más → Ajustes → Cuentas** tocá **Conectar con Google**, elegí la cuenta y apretá **Permitir**. Podés sumar varias.
Google va a avisar que la app "no está verificada": tocá **Configuración avanzada → Ir a Mate**. Es normal en apps personales.
Mientras la app esté en modo prueba, quien publica Mate tiene que sumar tu Gmail como "usuario de prueba", y cada 7 días
Google pide volver a conectar.

## Spotify
Funciona sin configurar nada: busca y reproduce lo que pidas y te devuelve el foco a lo que estabas haciendo.
Si tenés **Premium**, en Ajustes tocá **Conectar Spotify** y pone la música sin abrir la app (quien publica Mate te tiene que
sumar antes en su app de Spotify).

## Si algo no anda
- No te entiende o se activa solo: mirá `.mate\mate.log` (ahí queda lo que escuchó). En Ajustes podés bajar la sensibilidad
  de los aplausos o apagarlos.
- La interfaz se ve rara después de una actualización: cerrá Mate y abrilo de nuevo (borra la caché al arrancar).

## Privacidad
La IA, la voz y la memoria corren en tu compu. Solo sale a internet lo que pedís: Google, Slack, Spotify, clima, dólar y búsquedas.
Mate nunca manda nada (mails, Slack) sin tu confirmación y nunca borra archivos: van a la papelera o se pueden deshacer.
