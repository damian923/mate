#!/bin/bash
# Instalador de Mate para Mac. Se corre pegando esto en la Terminal:
#   curl -fsSL https://raw.githubusercontent.com/damian923/mate/main/instalar.sh | bash
set -e
REPO="damian923/mate"
APP="$HOME/Library/Application Support/Mate"
DATA="$HOME/.mate"
VENV="$DATA/venv"
mkdir -p "$DATA"

echo ""
echo "  Instalando Mate  (tarda un rato: baja unos 5 GB entre la IA y la voz)"
echo ""

echo "0/8 Bajando Mate..."
TMP="$(mktemp -d)"
curl -fsSL "https://github.com/$REPO/archive/refs/heads/main.zip" -o "$TMP/mate.zip"
unzip -q "$TMP/mate.zip" -d "$TMP"
mkdir -p "$APP"
cp -R "$TMP"/mate-main/. "$APP/"
rm -rf "$TMP"

echo "1/8 Homebrew (el instalador de programas de Mac)..."
if ! command -v brew >/dev/null 2>&1; then
  echo "   Te va a pedir la contraseña de tu Mac (es la de Homebrew, no la ve Mate)."
  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" </dev/tty
  eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
fi

echo "2/8 Python, Ollama y audio..."
brew install python@3.12 ollama portaudio >/dev/null
brew services start ollama >/dev/null || true
sleep 3

echo "3/8 Modelos de IA: Llama 3.2 3B (~2 GB) y Qwen3.5 2B de Unsloth (~1,9 GB)..."
ollama pull llama3.2:3b
ollama pull hf.co/unsloth/Qwen3.5-2B-GGUF:Q4_K_M

echo "4/8 Librerias de Python..."
PY="$(brew --prefix python@3.12)/bin/python3.12"
[ -d "$VENV" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -r "$APP/requirements.txt" -q

echo "5/8 Reconocimiento de voz: Vosk (~40 MB) y Whisper small (~480 MB)..."
if [ ! -d "$DATA/vosk-es" ]; then
  curl -fsSL "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip" -o "$DATA/vosk.zip"
  unzip -q "$DATA/vosk.zip" -d "$DATA" && mv "$DATA/vosk-model-small-es-0.42" "$DATA/vosk-es" && rm "$DATA/vosk.zip"
fi
"$VENV/bin/python" -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"
"$VENV/bin/python" -c "import onnx_asr; onnx_asr.load_model('nemo-parakeet-tdt-0.6b-v3', quantization='int8')"

echo "6/8 Voces de Mate (Piper, en espanol)..."
mkdir -p "$DATA/voces"
for v in es_AR/daniela/high/es_AR-daniela-high es_MX/claude/high/es_MX-claude-high es_MX/ald/medium/es_MX-ald-medium \
         es_ES/davefx/medium/es_ES-davefx-medium es_ES/sharvard/medium/es_ES-sharvard-medium; do
  n="$(basename "$v")"
  if [ ! -f "$DATA/voces/$n.onnx" ]; then
    curl -fsSL "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/$v.onnx" -o "$DATA/voces/$n.onnx"
    curl -fsSL "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/$v.onnx.json" -o "$DATA/voces/$n.onnx.json"
  fi
done

echo "7/8 Configuracion..."
[ -f "$DATA/config.json" ] || cp "$APP/config.ejemplo.json" "$DATA/config.json"

echo "8/8 Inicio automatico al prender la Mac..."
PLIST="$HOME/Library/LaunchAgents/ai.mate.plist"
mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.mate</string>
  <key>ProgramArguments</key><array><string>$VENV/bin/python</string><string>$APP/mate_lanzador.py</string></array>
  <key>WorkingDirectory</key><string>$APP</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
</dict></plist>
EOF
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo ""
echo "  Listo. Mate aparece arriba al centro de la pantalla y arranca solo al prender la Mac."
echo "  La primera vez macOS te va a pedir permisos: Microfono, Accesibilidad y Automatizacion. Acepta los tres."
echo "  Pasale el mouse por arriba, contale tu nombre y en Mas > Ajustes conecta Google y Spotify."
