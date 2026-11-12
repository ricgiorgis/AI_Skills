#!/usr/bin/env python3
"""OCR de documentos escaneados con PaddleOCR.

Convierte imágenes (JPG, PNG, TIFF multipágina, BMP, WEBP) y PDFs en texto
ordenado por lectura, con confianza y coordenadas por línea.

Uso:
    python3 ocr.py documento.pdf foto.jpg --out-dir salida/
    python3 ocr.py escaneo.pdf --engine paddle --lang es
    python3 ocr.py escaneo.pdf --min-conf 0.9 --rotate force

Salida por documento, en --out-dir:
    <nombre>.ocr.json   contrato estructurado (ver SKILL.md)
    <nombre>.ocr.md     texto por página; líneas dudosas marcadas ⟦texto · 0.62⟧
Y una línea JSON de resumen por documento en stdout.

Motores:
    onnx   (por defecto) PP-OCRv5 detección + reconocimiento latino en ONNX
           Runtime. Pesa ~13 MB de modelos, no necesita paddlepaddle (~700 MB)
           y baja los modelos de GitHub, que sí está permitido en los sandbox
           de Claude y ChatGPT. Los modelos se verifican por SHA-256.
    paddle PaddleOCR oficial con paddlepaddle. Elige el modelo según --lang
           (PP-OCRv6 para español en PaddleOCR 3.7). Baja los modelos de
           Hugging Face o BOS en el primer uso; en sandbox suele estar bloqueado.

Como librería:
    from ocr import ocr_document
    resultado = ocr_document("escaneo.pdf")   # dict con el mismo contrato
"""
import argparse
import hashlib
import json
import os
import statistics
import sys
import time
import urllib.request
from pathlib import Path

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
SCHEMA_VERSION = 1

# Modelos PP-OCRv5 en ONNX (conversión de GreatV/oar-ocr, Apache 2.0).
# Fijados a una release y verificados por hash: si cambian, falla en vez de
# cargar un modelo distinto.
ONNX_RELEASE = "https://github.com/GreatV/oar-ocr/releases/download/v0.3.0/"
ONNX_FILES = {
    "det": ("pp-ocrv5_mobile_det.onnx",
            "1eb7b4f7ab657ebd1c66d5f79bca7497f29768a2e3c15e52daecbba1a8e4a039"),
    "rec": ("latin_pp-ocrv5_mobile_rec.onnx",
            "e3a6bfeea1c8a01d6fccfd480a0bd363fd907f8c65931e228bb2736f5c3e142f"),
    "dict": ("ppocrv5_latin_dict.txt",
             "ccbcc45730b3fbbd9050c5bc74db6a99067141ef1035e3d14889a84a6b9b1aff"),
}
ONNX_DET_NAME = "PP-OCRv5_mobile_det"
ONNX_REC_NAME = "latin_PP-OCRv5_mobile_rec"
DEFAULT_MODELS_DIR = Path(os.environ.get(
    "OCR_DOCUMENTOS_MODELS", Path.home() / ".cache" / "ocr-documentos" / "ppocrv5"))


def log(msg):
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# Modelos
# --------------------------------------------------------------------------

def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _fetch(name, sha, dest):
    if dest.exists() and _sha256(dest) == sha:
        return
    log(f"Descargando {name} ...")
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(ONNX_RELEASE + name, tmp)
    got = _sha256(tmp)
    if got != sha:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"Hash no coincide para {name}: {got}")
    tmp.replace(dest)


def prepare_onnx_models(models_dir=DEFAULT_MODELS_DIR):
    """Deja det/ y rec/ con inference.onnx + inference.yml listos para PaddleX."""
    import yaml

    models_dir = Path(models_dir)
    raw = models_dir / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for key, (name, sha) in ONNX_FILES.items():
        _fetch(name, sha, raw / name)

    det, rec = models_dir / "det", models_dir / "rec"
    det.mkdir(exist_ok=True)
    rec.mkdir(exist_ok=True)
    for src, dst in ((raw / ONNX_FILES["det"][0], det / "inference.onnx"),
                     (raw / ONNX_FILES["rec"][0], rec / "inference.onnx")):
        if not dst.exists() or _sha256(dst) != _sha256(src):
            dst.write_bytes(src.read_bytes())

    # Configuración equivalente al inference.yml que exporta PaddleOCR.
    det_cfg = {
        "Global": {"model_name": ONNX_DET_NAME},
        "PreProcess": {"transform_ops": [
            {"DecodeImage": {"channel_first": False, "img_mode": "BGR"}},
            {"DetLabelEncode": None},
            {"DetResizeForTest": {"resize_long": 960}},
            {"NormalizeImage": {"mean": [0.485, 0.456, 0.406], "order": "hwc",
                                "scale": "1./255.", "std": [0.229, 0.224, 0.225]}},
            {"ToCHWImage": None},
            {"KeepKeys": {"keep_keys": ["image", "shape", "polys", "ignore_tags"]}},
        ]},
        "PostProcess": {"name": "DBPostProcess", "thresh": 0.3, "box_thresh": 0.6,
                        "max_candidates": 1000, "unclip_ratio": 1.5},
    }
    chars = [l.rstrip("\n") for l in
             (raw / ONNX_FILES["dict"][0]).read_text(encoding="utf-8").splitlines(True)]
    rec_cfg = {
        "Global": {"model_name": ONNX_REC_NAME},
        "PreProcess": {"transform_ops": [
            {"DecodeImage": {"channel_first": False, "img_mode": "BGR"}},
            {"MultiLabelEncode": {"gtc_encode": "NRTRLabelEncode"}},
            {"RecResizeImg": {"image_shape": [3, 48, 320]}},
            {"KeepKeys": {"keep_keys": ["image", "label_ctc", "label_gtc", "length", "valid_ratio"]}},
        ]},
        "PostProcess": {"name": "CTCLabelDecode", "character_dict": chars},
    }
    for path, cfg in ((det / "inference.yml", det_cfg), (rec / "inference.yml", rec_cfg)):
        path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return det, rec


def build_engine(engine="onnx", lang="es", models_dir=DEFAULT_MODELS_DIR, threads=None):
    """Crea el pipeline de PaddleOCR. Devuelve (ocr, info_de_modelos)."""
    os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
    from paddleocr import PaddleOCR

    common = dict(use_doc_orientation_classify=False, use_doc_unwarping=False,
                  use_textline_orientation=False)
    if threads:
        common["cpu_threads"] = threads
    if engine == "onnx":
        det, rec = prepare_onnx_models(models_dir)
        ocr = PaddleOCR(engine="onnxruntime",
                        text_detection_model_name=ONNX_DET_NAME, text_detection_model_dir=str(det),
                        text_recognition_model_name=ONNX_REC_NAME, text_recognition_model_dir=str(rec),
                        **common)
        return ocr, {"engine": "onnx", "det": ONNX_DET_NAME, "rec": ONNX_REC_NAME}
    if engine == "paddle":
        try:
            ocr = PaddleOCR(lang=lang, **common)
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if "model hosting" in msg or "model source" in msg or "download model" in msg:
                raise RuntimeError(
                    "PaddleOCR no pudo bajar sus modelos (Hugging Face/BOS bloqueados). "
                    "Usá --engine onnx.") from e
            raise
        names = {}
        try:
            subs = ocr.paddlex_pipeline._pipeline.config["SubModules"]  # type: ignore[attr-defined]
            names = {"det": subs["TextDetection"]["model_name"],
                     "rec": subs["TextRecognition"]["model_name"]}
        except Exception:  # noqa: BLE001
            pass
        return ocr, {"engine": "paddle", "lang": lang, **names}
    raise ValueError(f"Motor desconocido: {engine}")


# --------------------------------------------------------------------------
# Páginas
# --------------------------------------------------------------------------

def _pil_to_bgr(img):
    import numpy as np
    return np.ascontiguousarray(np.asarray(img.convert("RGB"))[:, :, ::-1])


def iter_pages(path, dpi=300, text_layer="auto", min_text_chars=50):
    """Produce dicts {page, kind, image|text, width, height}.

    kind = "ocr" (trae imagen BGR) o "text-layer" (PDF digital con texto real).
    """
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".pdf":
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(path))
        try:
            for i in range(len(pdf)):
                page = pdf[i]
                w, h = page.get_size()
                if text_layer == "auto":
                    txt = page.get_textpage().get_text_range() or ""
                    if len(txt.strip()) >= min_text_chars:
                        yield {"page": i + 1, "kind": "text-layer", "text": txt,
                               "width": round(w), "height": round(h)}
                        continue
                pil = page.render(scale=dpi / 72).to_pil()
                yield {"page": i + 1, "kind": "ocr", "image": _pil_to_bgr(pil),
                       "width": pil.width, "height": pil.height}
        finally:
            pdf.close()
    elif ext in IMAGE_EXT:
        from PIL import Image, ImageOps, ImageSequence
        with Image.open(path) as im:
            for i, frame in enumerate(ImageSequence.Iterator(im)):
                fr = ImageOps.exif_transpose(frame.copy())  # fotos de celular giradas
                yield {"page": i + 1, "kind": "ocr", "image": _pil_to_bgr(fr),
                       "width": fr.width, "height": fr.height}
    else:
        raise ValueError(f"Formato no soportado: {path.name}")


# --------------------------------------------------------------------------
# OCR y orden de lectura
# --------------------------------------------------------------------------

def _run(ocr, image):
    res = next(iter(ocr.predict(image)))
    j = res.json["res"]
    lines = []
    for text, score, box in zip(j["rec_texts"], j["rec_scores"], j["rec_boxes"]):
        text = text.strip()
        if not text:
            continue
        x0, y0, x1, y1 = (int(v) for v in box)
        lines.append({"text": text, "conf": round(float(score), 4), "bbox": [x0, y0, x1, y1]})
    return lines


def _horizontal(l):
    x0, y0, x1, y1 = l["bbox"]
    return (x1 - x0) >= (y1 - y0) or len(l["text"]) < 3


def _score(lines):
    # Solo cuenta líneas horizontales: PaddleOCR gira los recortes altos, así
    # que texto de lado puede leerse bien pero con cajas verticales, y eso
    # rompe el orden de lectura. La orientación buena es la de líneas acostadas.
    return sum(l["conf"] * len(l["text"]) for l in lines if _horizontal(l))


def _looks_wrong(lines):
    """Página sospechosa de estar girada: poco texto confiable o líneas de pie."""
    long_lines = [l for l in lines if len(l["text"]) >= 3]
    if not long_lines:
        return True
    vertical = sum(not _horizontal(l) for l in long_lines) / len(long_lines)
    mean = statistics.mean(l["conf"] for l in long_lines)
    return vertical > 0.3 or mean < 0.7


def _rotate(image, k):
    import numpy as np
    return np.ascontiguousarray(np.rot90(image, k=k))


def ocr_image(ocr, image, rotate="auto"):
    """OCR de una imagen BGR. Devuelve (líneas, grados_antihorario_aplicados).

    rotate="auto": solo si la página parece girada prueba 90/180/270.
    rotate="force": prueba las cuatro siempre. rotate="off": nunca.
    """
    lines = _run(ocr, image)
    if rotate == "off" or (rotate == "auto" and not _looks_wrong(lines)):
        return lines, 0
    best, best_deg = lines, 0
    for k in (1, 2, 3):
        cand = _run(ocr, _rotate(image, k))
        if _score(cand) > _score(best) * 1.15:  # margen para no girar por ruido
            best, best_deg = cand, 90 * k
    return best, best_deg


def verify_targets(lines):
    """Líneas que hay que cotejar contra la imagen aunque tengan confianza alta:
    cifras, montos, fechas, números de registro y nombres en mayúsculas.
    El OCR confunde Q/0, O/0, l/1/I, S/5, B/8 y pega o parte nombres sin bajar
    la confianza de la línea."""
    out = []
    for l in lines:
        t = l["text"]
        caps = [w for w in t.replace(",", " ").split() if len(w) > 2 and w.isupper()]
        if any(c.isdigit() for c in t) or any(s in t for s in "Q$€%") or len(caps) >= 2:
            out.append(l["id"])
    return out


def reading_order(lines):
    """Agrupa líneas en filas (solapamiento vertical) y ordena izquierda→derecha.

    Devuelve una lista de filas; cada fila es una lista de líneas. Sirve para
    que las tablas salgan como "Concepto | Valor" y no al revés.
    """
    if not lines:
        return []
    items = sorted(lines, key=lambda l: (l["bbox"][1] + l["bbox"][3]) / 2)
    rows = []
    for l in items:
        y0, y1 = l["bbox"][1], l["bbox"][3]
        h = max(1, y1 - y0)
        placed = False
        for row in rows[-3:]:  # solo filas cercanas
            ry0, ry1 = row["y0"], row["y1"]
            overlap = min(y1, ry1) - max(y0, ry0)
            if overlap > 0.5 * min(h, ry1 - ry0):
                row["lines"].append(l)
                row["y0"], row["y1"] = min(ry0, y0), max(ry1, y1)
                placed = True
                break
        if not placed:
            rows.append({"y0": y0, "y1": y1, "lines": [l]})
    rows.sort(key=lambda r: r["y0"])
    return [sorted(r["lines"], key=lambda l: l["bbox"][0]) for r in rows]


def _fmt(line, min_conf):
    if line["conf"] is not None and line["conf"] < min_conf:
        return f"⟦{line['text']} · {line['conf']:.2f}⟧"
    return line["text"]


def rows_to_markdown(rows, min_conf):
    if not rows:
        return ""
    heights = [max(l["bbox"][3] for l in r) - min(l["bbox"][1] for l in r) for r in rows]
    med_h = statistics.median(heights) or 1
    out, prev_bottom = [], None
    for r in rows:
        top = min(l["bbox"][1] for l in r)
        if prev_bottom is not None and top - prev_bottom > 1.2 * med_h:
            out.append("")  # separación de párrafo
        parts, prev_x1 = [], None
        for l in r:
            if prev_x1 is not None:
                parts.append(" | " if l["bbox"][0] - prev_x1 > 1.5 * med_h else " ")
            parts.append(_fmt(l, min_conf))
            prev_x1 = l["bbox"][2]
        out.append("".join(parts))
        prev_bottom = max(l["bbox"][3] for l in r)
    return "\n".join(out)


# --------------------------------------------------------------------------
# Documento
# --------------------------------------------------------------------------

def ocr_document(path, ocr=None, model_info=None, engine="onnx", lang="es",
                 dpi=300, min_conf=0.85, text_layer="auto", rotate="auto",
                 models_dir=DEFAULT_MODELS_DIR):
    """OCR de un archivo completo. Devuelve el dict del contrato (ver SKILL.md)."""
    t0 = time.time()
    if ocr is None:
        ocr, model_info = build_engine(engine, lang, models_dir)
    pages = []
    for p in iter_pages(path, dpi=dpi, text_layer=text_layer):
        if p["kind"] == "text-layer":
            lines = [{"text": t.strip(), "conf": None, "bbox": None}
                     for t in p["text"].splitlines() if t.strip()]
            pages.append({"page": p["page"], "method": "text-layer", "rotation": 0,
                          "width": p["width"], "height": p["height"],
                          "lines": lines, "low_conf": [], "verify": [],
                          "markdown": "\n".join(l["text"] for l in lines)})
            continue
        lines, deg = ocr_image(ocr, p["image"], rotate=rotate)
        rows = reading_order(lines)
        ordered = [l for r in rows for l in r]
        for i, l in enumerate(ordered):
            l["id"] = i
        low = [l["id"] for l in ordered if l["conf"] < min_conf]
        w, h = (p["height"], p["width"]) if deg in (90, 270) else (p["width"], p["height"])
        pages.append({"page": p["page"], "method": "ocr", "rotation": deg,
                      "width": w, "height": h, "lines": ordered, "low_conf": low,
                      "verify": verify_targets(ordered),
                      "markdown": rows_to_markdown(rows, min_conf)})
    ocr_lines = [l for p in pages if p["method"] == "ocr" for l in p["lines"]]
    confs = [l["conf"] for l in ocr_lines]
    return {
        "schema": SCHEMA_VERSION,
        "source": str(Path(path).name),
        "models": model_info,
        "min_conf": min_conf,
        "pages": pages,
        "stats": {
            "pages": len(pages),
            "ocr_pages": sum(p["method"] == "ocr" for p in pages),
            "text_layer_pages": sum(p["method"] == "text-layer" for p in pages),
            "lines": len(ocr_lines),
            "low_conf_lines": sum(len(p["low_conf"]) for p in pages),
            "verify_lines": sum(len(p["verify"]) for p in pages),
            "mean_conf": round(statistics.mean(confs), 4) if confs else None,
            "min_line_conf": round(min(confs), 4) if confs else None,
            "seconds": round(time.time() - t0, 1),
        },
    }


def to_markdown(doc):
    s = doc["stats"]
    head = [f"<!-- OCR: {doc['source']} | motor {doc['models'].get('engine')} "
            f"({doc['models'].get('det', '?')} + {doc['models'].get('rec', '?')}) | "
            f"{s['pages']} pág. | {s['low_conf_lines']} líneas bajo {doc['min_conf']} "
            f"marcadas ⟦texto · conf⟧ -->"]
    for p in doc["pages"]:
        tag = "capa de texto del PDF" if p["method"] == "text-layer" else "OCR"
        rot = f", corregida {p['rotation']}° antihorario" if p["rotation"] else ""
        head.append(f"\n## Página {p['page']} ({tag}{rot})\n")
        head.append(p["markdown"] or "_(sin texto detectado)_")
        if p["verify"]:
            by_id = {l["id"]: l for l in p["lines"]}
            head.append(f"\n### Cotejar contra la imagen (página {p['page']})\n")
            head.extend(f"- [{i}] {by_id[i]['text']}" for i in p["verify"])
    return "\n".join(head) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="OCR de documentos escaneados con PaddleOCR")
    ap.add_argument("inputs", nargs="+", help="PDFs o imágenes")
    ap.add_argument("--out-dir", default="ocr_out")
    ap.add_argument("--engine", choices=["onnx", "paddle"], default="onnx")
    ap.add_argument("--lang", default="es", help="solo para --engine paddle")
    ap.add_argument("--dpi", type=int, default=300, help="resolución al rasterizar PDFs")
    ap.add_argument("--min-conf", type=float, default=0.85,
                    help="debajo de esto la línea se marca para revisión")
    ap.add_argument("--text-layer", choices=["auto", "ignore"], default="auto",
                    help="auto: usa el texto real de PDFs digitales; ignore: OCR a todo")
    ap.add_argument("--rotate", choices=["auto", "force", "off"], default="auto",
                    help="auto: prueba otras orientaciones solo si la página parece girada")
    ap.add_argument("--models-dir", default=str(DEFAULT_MODELS_DIR))
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--prepare-only", action="store_true",
                    help="solo baja y prepara los modelos ONNX")
    a = ap.parse_args(argv)

    if a.prepare_only:
        det, rec = prepare_onnx_models(a.models_dir)
        log(f"Modelos listos en {Path(a.models_dir)}")
        return 0

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ocr, info = build_engine(a.engine, a.lang, a.models_dir, a.threads)
    rc = 0
    for inp in a.inputs:
        try:
            doc = ocr_document(inp, ocr=ocr, model_info=info, dpi=a.dpi, min_conf=a.min_conf,
                               text_layer=a.text_layer, rotate=a.rotate)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"source": Path(inp).name, "error": str(e)}, ensure_ascii=False))
            rc = 1
            continue
        stem = Path(inp).stem
        (out / f"{stem}.ocr.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        (out / f"{stem}.ocr.md").write_text(to_markdown(doc), encoding="utf-8")
        print(json.dumps({"source": doc["source"], "json": str(out / f"{stem}.ocr.json"),
                          "md": str(out / f"{stem}.ocr.md"), **doc["stats"]}, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())
