"""Mi PC: ordenar el escritorio (con opciones), deshacer y alinear íconos. Nunca borra nada."""
import ctypes, re, shutil, subprocess
from pathlib import Path

TIPOS = {"Imágenes": "jpg jpeg png gif webp bmp svg heic tif tiff ico", "Documentos": "pdf doc docx txt odt rtf md",
         "Planillas": "xls xlsx xlsm csv ods", "Presentaciones": "ppt pptx key odp", "Videos": "mp4 mov avi mkv webm",
         "Audio": "mp3 wav m4a flac ogg", "Comprimidos": "zip rar 7z tar gz", "Instaladores": "exe msi",
         "Código": "py js ts html css json ipynb"}
EXT = {e: carpeta for carpeta, exts in TIPOS.items() for e in exts.split()}
ACCESOS = {".lnk", ".url"}
CAPTURA = re.compile(r"^(captura de pantalla|screenshot|recorte de pantalla|snip)", re.I)

DEFAULTS = {"ordenado": True, "capturas": True, "accesos": False, "alinear": True}


def desktop():
    """La carpeta real del escritorio (puede estar en OneDrive)."""
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                             capture_output=True, text=True, timeout=10, creationflags=0x08000000).stdout.strip()
        if out and Path(out).exists(): return Path(out)
    except Exception:
        pass
    return Path.home() / "Desktop"


def plan(opts=None):
    """Devuelve [(archivo, carpeta destino relativa)] sin mover nada."""
    o = dict(DEFAULTS, **(opts or {}))
    base = desktop()
    root = Path("Ordenado") if o["ordenado"] else Path(".")
    out = []
    for f in base.iterdir():
        if not f.is_file() or f.name.lower() == "desktop.ini" or f.name.startswith("~$"):
            continue
        ext = f.suffix.lower()
        if ext in ACCESOS:
            if o["accesos"]: out.append((f, root / "Accesos directos"))
            continue
        if o["capturas"] and CAPTURA.match(f.stem):
            out.append((f, root / "Capturas")); continue
        if ext[1:] in EXT:
            out.append((f, root / EXT[ext[1:]]))
    return out


def apply(items):
    """Mueve los archivos. Devuelve la lista para deshacer [(nuevo, original)]."""
    base, moved = desktop(), []
    for f, rel in items:
        dst_dir = base / rel
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / f.name
        n = 2
        while dst.exists():   # nunca pisa: agrega (2), (3)…
            dst = dst_dir / f"{f.stem} ({n}){f.suffix}"; n += 1
        shutil.move(str(f), dst); moved.append([str(dst), str(f)])
    return moved


def undo(moved):
    back = 0
    for dst, src in moved or []:
        if Path(dst).exists() and not Path(src).exists():
            shutil.move(dst, src); back += 1
    # borra las carpetas que quedaron vacías (solo las que creó Mate)
    base = desktop()
    for d in sorted({Path(dst).parent for dst, _ in moved or []}, key=lambda p: -len(str(p))):
        try:
            if d.exists() and d != base and not any(d.iterdir()): d.rmdir()
            if d.parent.name == "Ordenado" and d.parent.exists() and not any(d.parent.iterdir()): d.parent.rmdir()
        except Exception:
            pass
    return back


def align_icons():
    """Alinea los íconos del escritorio en columnas desde arriba a la izquierda (como 'Organizar automáticamente')."""
    u = ctypes.windll.user32
    lv = 0
    progman = u.FindWindowW("Progman", None)
    view = u.FindWindowExW(progman, 0, "SHELLDLL_DefView", None)
    if not view:   # con fondo animado/varios monitores el escritorio vive en un WorkerW
        def cb(h, _):
            nonlocal view
            v = u.FindWindowExW(h, 0, "SHELLDLL_DefView", None)
            if v: view = v
            return True
        u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(cb), 0)
    if view: lv = u.FindWindowExW(view, 0, "SysListView32", None)
    if not lv: return False
    LVM_ARRANGE, LVA_SNAPTOGRID, LVA_ALIGNTOP = 0x1016, 5, 1
    u.SendMessageW(lv, LVM_ARRANGE, LVA_ALIGNTOP, 0)
    u.SendMessageW(lv, LVM_ARRANGE, LVA_SNAPTOGRID, 0)
    return True
