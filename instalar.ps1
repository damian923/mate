# Instalador de Mate. Se puede correr de dos formas:
#   1) Con un link (no hace falta bajar nada antes), pegando esto en PowerShell:
#        irm https://raw.githubusercontent.com/damian923/mate/main/instalar.ps1 | iex
#   2) Desde la carpeta de Mate: doble click en INSTALAR.bat
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"
$repo = "damian923/mate"   # usuario/repositorio en GitHub (lo completa quien publica Mate)
$dir = "$env:USERPROFILE\.mate"
New-Item -ItemType Directory -Force $dir | Out-Null

Write-Host ""
Write-Host "  Instalando Mate  (tarda un rato: baja unos 5 GB entre la IA y la voz)" -ForegroundColor Yellow
Write-Host ""

# --- 0. El codigo de Mate ---
$app = $PSScriptRoot
if (-not $app -or -not (Test-Path (Join-Path $app "mate.py"))) {
    Write-Host "0/8 Bajando Mate..."
    $app = "$env:LOCALAPPDATA\Mate"
    $tmp = "$env:TEMP\mate-src.zip"
    Invoke-WebRequest "https://github.com/$repo/archive/refs/heads/main.zip" -OutFile $tmp
    $ex = "$env:TEMP\mate-src"
    Remove-Item $ex -Recurse -Force -ErrorAction SilentlyContinue
    Expand-Archive $tmp $ex -Force
    New-Item -ItemType Directory -Force $app | Out-Null
    Copy-Item "$((Get-ChildItem $ex -Directory)[0].FullName)\*" $app -Recurse -Force
    Remove-Item $tmp, $ex -Recurse -Force
}

function Refresh-Path { $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User") }

Write-Host "1/8 Python..."
if (-not (Get-Command python -ErrorAction SilentlyContinue) -or (python --version 2>&1) -notmatch "Python 3") {
    winget install -e --id Python.Python.3.12 --scope user --accept-source-agreements --accept-package-agreements --silent
    Refresh-Path
}

Write-Host "2/8 Ollama (corre la IA en tu compu)..."
if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements --silent
    Refresh-Path; $env:Path += ";$env:LOCALAPPDATA\Programs\Ollama"
}

Write-Host "3/8 Modelos de IA: Llama 3.2 3B (pedidos, ~2 GB) y Qwen3.5 2B de Unsloth (reuniones y textos largos, ~1,9 GB)..."
ollama pull llama3.2:3b
ollama pull hf.co/unsloth/Qwen3.5-2B-GGUF:Q4_K_M

Write-Host "4/8 Librerias de Python..."
python -m pip install --upgrade pip -q
python -m pip install -r "$app\requirements.txt"

Write-Host "5/8 Reconocimiento de voz: Vosk (palabra 'Mate', ~40 MB) y Whisper small (~480 MB)..."
if (-not (Test-Path "$dir\vosk-es")) {
    $zip = "$dir\vosk.zip"
    Invoke-WebRequest "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip" -OutFile $zip
    Expand-Archive $zip $dir -Force
    Rename-Item "$dir\vosk-model-small-es-0.42" "vosk-es"
    Remove-Item $zip
}
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
python -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')"

Write-Host "6/8 Voces de Mate (Piper, en espanol)..."
$vdir = Join-Path $dir "voces"
New-Item -ItemType Directory -Force $vdir | Out-Null
foreach ($v in "es_AR/daniela/high/es_AR-daniela-high", "es_MX/claude/high/es_MX-claude-high", "es_MX/ald/medium/es_MX-ald-medium",
               "es_ES/davefx/medium/es_ES-davefx-medium", "es_ES/sharvard/medium/es_ES-sharvard-medium") {
    $n = Split-Path $v -Leaf
    if (-not (Test-Path "$vdir\$n.onnx")) {
        Invoke-WebRequest "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/$v.onnx" -OutFile "$vdir\$n.onnx"
        Invoke-WebRequest "https://huggingface.co/rhasspy/piper-voices/resolve/main/es/$v.onnx.json" -OutFile "$vdir\$n.onnx.json"
    }
}

Write-Host "7/8 Configuracion..."
if (-not (Test-Path "$dir\config.json")) { Copy-Item "$app\config.ejemplo.json" "$dir\config.json" }

Write-Host "8/8 Inicio automatico con Windows..."
$pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
$lnk = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Mate.lnk"
$s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
$s.TargetPath = $pyw; $s.Arguments = "`"$app\mate.py`""; $s.WorkingDirectory = $app; $s.Save()
Copy-Item $lnk "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Mate.lnk" -Force   # tambien en el menu Inicio

Write-Host ""
Write-Host "  Listo. Mate arranca solo al prender la compu (lo ves arriba al centro de la pantalla)." -ForegroundColor Green
Write-Host "  Pasale el mouse por arriba, contale tu nombre y en Mas > Ajustes conecta Google y Spotify."
Start-Process $pyw -ArgumentList "`"$app\mate.py`"" -WorkingDirectory $app
