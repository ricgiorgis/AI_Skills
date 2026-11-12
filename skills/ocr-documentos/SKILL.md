---
name: ocr-documentos
description: "OCR de documentos escaneados, fotos de documentos y PDFs sin texto con PaddleOCR: texto en orden de lectura, confianza por línea, coordenadas y lista de cifras y nombres a cotejar. Usar cuando haya que extraer, transcribir o digitalizar texto de un escaneo, una foto de celular, un fax o un PDF escaneado (partidas, certificaciones, títulos, contratos, facturas, manifiestos, formularios), y como paso previo a una traducción jurada de documentos escaneados largos o con muchas cifras."
---

# OCR de documentos

Convierte escaneos y fotos de documentos en texto estructurado con PaddleOCR (PP-OCRv5). Todo lo hace `scripts/ocr.py`; esta guía dice cuándo usarlo, cómo leer la salida y qué verificar antes de confiar en ella.

## Cuándo sí y cuándo no

- **Sí:** PDFs escaneados, fotos de celular de documentos, TIFF multipágina de escáner, lotes de varios documentos, cualquier documento donde importen las cifras (números de registro, montos, fechas, DPI, códigos).
- **Sí, como segunda lectura:** aunque Claude pueda leer la imagen directamente, el OCR da una lectura independiente y literal. Las dos juntas atrapan errores que cada una por separado deja pasar.
- **No hace falta:** PDFs digitales (Word exportado, facturas electrónicas). El script los detecta y usa la capa de texto real sin OCR.
- **No sirve:** manuscritos, firmas, texto curvo de sellos redondos (sale parcial o mal). Eso se transcribe mirando la imagen.

## Instalación

Motor por defecto `onnx`: no necesita paddlepaddle.

```bash
pip install "paddleocr>=3.7" "paddlex[ocr-core]" onnxruntime
# en sandbox de Claude/ChatGPT agregar --break-system-packages
```

La primera corrida baja ~13 MB de modelos desde una release fija de GitHub (GreatV/oar-ocr v0.3.0, Apache 2.0), verifica el SHA-256 de cada archivo y los guarda en `~/.cache/ocr-documentos/` (o en `$OCR_DOCUMENTOS_MODELS`). Si el hash no coincide, falla en vez de cargar otro modelo. `--prepare-only` solo baja los modelos.

Motor `paddle`: PaddleOCR oficial, elige modelo por `--lang` (PP-OCRv6 para español en PaddleOCR 3.7) y sirve para alfabetos no latinos. Requiere además `pip install paddlepaddle` (~700 MB) y acceso a Hugging Face o BOS para bajar modelos, que en los sandbox está bloqueado. Fuera de sandbox es una opción válida; dentro, usar `onnx`.

## Uso

```bash
python3 <SKILL_DIR>/scripts/ocr.py escaneo.pdf foto1.jpg foto2.jpg --out-dir ocr_out
```

| Opción | Default | Qué hace |
|---|---|---|
| `--engine` | `onnx` | `onnx` (PP-OCRv5 latino, liviano) o `paddle` (oficial, multilingüe) |
| `--lang` | `es` | solo con `--engine paddle` |
| `--dpi` | `300` | resolución al rasterizar PDFs; subir a 400 si la letra es muy chica |
| `--min-conf` | `0.85` | líneas debajo de esto se marcan para revisión |
| `--text-layer` | `auto` | `auto` usa el texto real de PDFs digitales; `ignore` fuerza OCR (PDFs de escáner con capa de texto mala) |
| `--rotate` | `auto` | `auto` prueba otras orientaciones solo si la página parece girada; `force` las prueba siempre; `off` nunca |

Velocidad de referencia en 2 CPU: ~4 s por página; una página girada cuesta ~4 veces más porque prueba las cuatro orientaciones. Las fotos de celular se enderezan primero con la orientación EXIF.

El motor `onnx` reconoce alfabeto latino (español, inglés, francés, portugués, italiano, alemán y otros). Para otros alfabetos, `--engine paddle --lang <código>`.

## Salida

Por cada archivo, en `--out-dir`:

- `<nombre>.ocr.md`: texto por página en orden de lectura. Las celdas de una misma fila de tabla salen separadas por ` | `. Las líneas bajo `--min-conf` salen como `⟦texto · 0.62⟧`. Al final de cada página, una sección **Cotejar contra la imagen** con las líneas que llevan cifras, montos o nombres en mayúsculas.
- `<nombre>.ocr.json`: el contrato estructurado (schema 1).
- stdout: una línea JSON de resumen por documento (`lines`, `low_conf_lines`, `verify_lines`, `mean_conf`, `min_line_conf`, `seconds`).

```json
{
  "schema": 1,
  "source": "partida.pdf",
  "models": {"engine": "onnx", "det": "PP-OCRv5_mobile_det", "rec": "latin_PP-OCRv5_mobile_rec"},
  "min_conf": 0.85,
  "pages": [{
    "page": 1, "method": "ocr", "rotation": 0, "width": 2480, "height": 3508,
    "lines": [{"id": 0, "text": "REGISTRO NACIONAL", "conf": 0.9956, "bbox": [417, 186, 1318, 275]}],
    "low_conf": [12],
    "verify": [0, 4, 5, 16],
    "markdown": "..."
  }],
  "stats": {"pages": 1, "ocr_pages": 1, "text_layer_pages": 0, "lines": 22,
            "low_conf_lines": 1, "verify_lines": 9, "mean_conf": 0.99, "min_line_conf": 0.92, "seconds": 4.0}
}
```

`method` es `ocr` o `text-layer`. En páginas `text-layer`, `conf` y `bbox` son `null` y `verify` va vacío porque el texto es el real del PDF. `rotation` son los grados antihorario que se aplicaron para enderezar; `bbox` está en píxeles de la imagen ya enderezada. Los `id` siguen el orden de lectura.

## La confianza no basta: doble lectura obligatoria

En las pruebas, una copia degradada de una certificación salió con `015.00` donde decía `Q15.00`, `MARIA` sin tilde y `MARÍAJOSÉ` pegado, todo con confianza de 0.95 o más. El OCR confunde Q/0, O/0, l/1/I, S/5 y B/8, se come tildes y pega o parte nombres sin que baje la confianza de la línea. Por eso:

1. Correr el script y revisar el resumen. `mean_conf` bajo 0.9 o muchas `low_conf_lines` indican un escaneo malo: avisar al usuario antes de seguir, y si se puede, pedir uno mejor.
2. Mirar la imagen de cada página y cotejar contra ella **todas** las líneas de `verify` y de `low_conf`, carácter por carácter en cifras y nombres.
3. Si la imagen y el OCR no coinciden, gana lo que se ve en la imagen. Si la imagen tampoco deja claro el carácter, no se adivina: se reporta al usuario como ilegible, con página y línea.
4. Al entregar, decir cuántas líneas se cotejaron y cuáles se corrigieron o quedaron como ilegibles.

## Como paso previo a traducción jurada

Útil cuando el documento escaneado es largo o trae muchas cifras: da el texto fuente literal y una lista concreta de datos a cotejar, en vez de transcribir de la imagen línea por línea.

1. Correr el OCR sobre el documento del cliente.
2. Hacer la doble lectura de arriba sobre las líneas de `verify` y `low_conf`.
3. Usar el `.ocr.md` corregido como texto fuente para la traducción, con las reglas de la skill de traducción jurada intactas.
4. Las partes que el OCR no cubre (sellos, firmas, timbres, notas manuscritas) se describen mirando la imagen, como siempre en una traducción jurada.

El OCR nunca pasa sin cotejo a una traducción jurada: un número de registro mal leído invalida el documento.

## Como librería

Para backends o lotes grandes, crear el motor una sola vez (cargar modelos es lo caro) y reutilizarlo:

```python
import sys; sys.path.insert(0, "<SKILL_DIR>/scripts")
from ocr import build_engine, ocr_document, to_markdown

ocr, info = build_engine("onnx")
for path in archivos:
    doc = ocr_document(path, ocr=ocr, model_info=info)   # dict del contrato
    texto = to_markdown(doc)
```

## Límites conocidos

- Tablas: se reconstruyen filas (celdas con ` | `), no la estructura completa con celdas combinadas.
- Documentos a dos columnas: las filas se arman por altura, así que dos columnas de prosa pueden mezclarse en una misma línea. En ese caso, leer el JSON por `bbox` o cotejar con la imagen.
- Sellos redondos y texto curvo: lectura parcial.
- Manuscrito: no confiable.
- Imágenes muy chicas o borrosas: subir `--dpi` en PDFs; en fotos, pedir otra toma.
