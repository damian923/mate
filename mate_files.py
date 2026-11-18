"""Acciones sobre archivos que soltás en la isla: analizar, ordenar, convertir, enviar, papelera."""
import shutil, subprocess, zipfile, re
from pathlib import Path

IMG = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".gif", ".ico", ".heic"}
TEXTO = {".txt", ".md", ".csv", ".json", ".py", ".js", ".html", ".css", ".log", ".xml"}
FORMATOS_IMG = ["JPG", "PNG", "WEBP", "BMP", "TIFF", "PDF"]

HOME = Path.home()
DESTINOS = {"Imágenes": HOME / "Pictures", "Documentos": HOME / "Documents", "Videos": HOME / "Videos",
            "Música": HOME / "Music"}


def kind(p):
    e = Path(p).suffix.lower()
    if e in IMG: return "imagen"
    if e == ".pdf": return "pdf"
    if e in (".docx",): return "word"
    if e in (".xlsx", ".xlsm"): return "excel"
    if e in (".pptx",): return "powerpoint"
    if e in TEXTO: return "texto"
    if e in (".mp3", ".wav", ".m4a", ".flac", ".ogg"): return "audio"
    if e in (".mp4", ".mov", ".mkv", ".avi", ".webm"): return "video"
    return "otro"


def formats(p):
    """A qué se puede convertir: lo más común primero (PDF→Word, Word→PDF, JPG→PNG, PNG→JPG)."""
    e = Path(p).suffix.lower()
    if e == ".pdf": return ["WORD"]
    if e == ".docx": return ["PDF"]
    if e in (".jpg", ".jpeg"): return ["PNG", "PDF"]
    if e == ".png": return ["JPG", "PDF"]
    if e in IMG: return ["JPG", "PNG"]
    return []


def info(paths):
    return [{"path": str(Path(p)), "name": Path(p).name, "kind": kind(p),
             "size": Path(p).stat().st_size if Path(p).exists() else 0, "formats": formats(p)} for p in paths]


def _free(p, ext):
    dst = p.with_suffix(ext); n = 2
    while dst.exists(): dst = p.with_name(f"{p.stem} ({n}){ext}"); n += 1
    return dst


def _xml_text(zpath, inner_glob):
    with zipfile.ZipFile(zpath) as z:
        names = sorted(n for n in z.namelist() if re.match(inner_glob, n))
        return "\n".join(re.sub(r"<[^>]+>", " ", z.read(n).decode("utf-8", "ignore")) for n in names)


def read_text(p, limit=60000):
    """Saca el texto de un archivo para que la IA lo analice."""
    p = Path(p); k = kind(p)
    try:
        if k == "texto": return p.read_text(encoding="utf-8", errors="ignore")[:limit]
        if k == "word": return re.sub(r"\s+", " ", _xml_text(p, r"word/document\.xml"))[:limit]
        if k == "powerpoint": return re.sub(r"\s+", " ", _xml_text(p, r"ppt/slides/slide\d+\.xml"))[:limit]
        if k == "excel":
            shared = _xml_text(p, r"xl/sharedStrings\.xml")
            return re.sub(r"\s+", " ", shared)[:limit]
        if k == "pdf":
            try:
                from pypdf import PdfReader
                return "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)[:limit]
            except ImportError:
                return ""
    except Exception:
        return ""
    return ""


def describe_image(p):
    from PIL import Image
    im = Image.open(p)
    return f"Imagen {im.width}×{im.height} px, formato {im.format}, modo {im.mode}"


def convert(p, fmt):
    """Convierte a otro formato. Guarda la copia al lado del original (no lo borra)."""
    p = Path(p); fmt = fmt.upper()
    if p.suffix.lower() == ".pdf" and fmt == "WORD":
        from pdf2docx import Converter
        dst = _free(p, ".docx"); cv = Converter(str(p)); cv.convert(str(dst)); cv.close()
        return dst
    if p.suffix.lower() == ".docx" and fmt == "PDF":
        dst = _free(p, ".pdf")
        try:
            import pythoncom; pythoncom.CoInitialize()
            from docx2pdf import convert as d2p
            d2p(str(p), str(dst))
        except Exception as e:
            raise RuntimeError("para pasar de Word a PDF hace falta tener Microsoft Word instalado") from e
        return dst
    from PIL import Image
    ext = {"JPG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp", "TIFF": ".tiff", "PDF": ".pdf"}[fmt]
    dst = _free(p, ext)
    im = Image.open(p)
    if fmt in ("JPG", "PDF", "BMP") and im.mode in ("RGBA", "P", "LA"):
        bg = Image.new("RGB", im.size, "white"); bg.paste(im.convert("RGBA"), mask=im.convert("RGBA").split()[-1]); im = bg
    im.save(dst, "JPEG" if fmt == "JPG" else fmt, **({"quality": 92} if fmt in ("JPG", "WEBP") else {}))
    return dst


def organize(p):
    """Mueve el archivo a la carpeta que le corresponde (Imágenes, Documentos, Videos, Música)."""
    p = Path(p); k = kind(p)
    dest = DESTINOS["Imágenes"] if k == "imagen" else DESTINOS["Videos"] if k == "video" else \
        DESTINOS["Música"] if k == "audio" else DESTINOS["Documentos"]
    dest.mkdir(parents=True, exist_ok=True)
    dst = dest / p.name; n = 2
    while dst.exists(): dst = dest / f"{p.stem} ({n}){p.suffix}"; n += 1
    shutil.move(str(p), dst)
    return dst


def trash(p):
    """A la papelera de reciclaje (se puede recuperar), nunca borrado definitivo."""
    from send2trash import send2trash
    send2trash(str(p))
