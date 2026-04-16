import io
import logging
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)


class OCREngine:
    """Dual OCR engine: EasyOCR (primary) + DocTR (secondary)."""

    def __init__(self, use_gpu: bool = False):
        self.use_gpu = use_gpu
        self._easyocr_reader = None
        self._doctr_model = None

    # ------------------------------------------------------------------ #
    #  Lazy initializers                                                   #
    # ------------------------------------------------------------------ #

    def _get_easyocr(self):
        if self._easyocr_reader is None:
            logger.info("Initialising EasyOCR …")
            import easyocr
            self._easyocr_reader = easyocr.Reader(["en"], gpu=self.use_gpu)
        return self._easyocr_reader

    def _get_doctr(self):
        if self._doctr_model is None:
            logger.info("Initialising DocTR …")
            try:
                from doctr.models import ocr_predictor
                self._doctr_model = ocr_predictor(pretrained=True)
            except Exception as exc:
                logger.warning("DocTR unavailable (%s) – falling back to EasyOCR only.", exc)
                self._doctr_model = False          # sentinel: tried & failed
        return self._doctr_model if self._doctr_model else None

    # ------------------------------------------------------------------ #
    #  PDF → images                                                        #
    # ------------------------------------------------------------------ #

    @staticmethod
    def pdf_to_images(pdf_bytes: bytes):
        """Convert every page of a PDF to a PIL RGB image (2× zoom for OCR)."""
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        images = []
        mat = fitz.Matrix(2.0, 2.0)
        for page in doc:
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
            img = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            images.append(img)
        return images

    # ------------------------------------------------------------------ #
    #  Pre-processing                                                      #
    # ------------------------------------------------------------------ #

    @staticmethod
    def preprocess(img: Image.Image) -> np.ndarray:
        """Light contrast enhancement + denoising for better OCR accuracy."""
        import cv2
        arr = np.array(img)
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        denoised = cv2.fastNlMeansDenoising(enhanced, h=10)
        # Return as RGB so both engines receive consistent input
        rgb = cv2.cvtColor(denoised, cv2.COLOR_GRAY2RGB)
        return rgb

    # ------------------------------------------------------------------ #
    #  EasyOCR                                                             #
    # ------------------------------------------------------------------ #

    def run_easyocr(self, img: Image.Image):
        reader = self._get_easyocr()
        arr = self.preprocess(img)
        raw = reader.readtext(arr, detail=1, paragraph=False)
        results = []
        for (bbox, text, conf) in raw:
            x1 = min(p[0] for p in bbox)
            y1 = min(p[1] for p in bbox)
            x2 = max(p[0] for p in bbox)
            y2 = max(p[1] for p in bbox)
            if text.strip():
                results.append({
                    "text": text.strip(),
                    "x1": float(x1), "y1": float(y1),
                    "x2": float(x2), "y2": float(y2),
                    "conf": float(conf),
                    "source": "easyocr",
                })
        return results

    # ------------------------------------------------------------------ #
    #  DocTR                                                               #
    # ------------------------------------------------------------------ #

    def run_doctr(self, img: Image.Image):
        model = self._get_doctr()
        if model is None:
            return []
        try:
            from doctr.io import DocumentFile
            img_w, img_h = img.size
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            doc = DocumentFile.from_images([buf.read()])
            result = model(doc)
            words = []
            for page in result.pages:
                for block in page.blocks:
                    for line in block.lines:
                        for word in line.words:
                            g = word.geometry          # ((x1n,y1n),(x2n,y2n)) normalised
                            x1 = g[0][0] * img_w
                            y1 = g[0][1] * img_h
                            x2 = g[1][0] * img_w
                            y2 = g[1][1] * img_h
                            if word.value.strip():
                                words.append({
                                    "text": word.value.strip(),
                                    "x1": float(x1), "y1": float(y1),
                                    "x2": float(x2), "y2": float(y2),
                                    "conf": float(word.confidence),
                                    "source": "doctr",
                                })
            return words
        except Exception as exc:
            logger.error("DocTR inference failed: %s", exc)
            return []

    # ------------------------------------------------------------------ #
    #  Merge                                                               #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _iou(a: dict, b: dict) -> float:
        ix1 = max(a["x1"], b["x1"])
        iy1 = max(a["y1"], b["y1"])
        ix2 = min(a["x2"], b["x2"])
        iy2 = min(a["y2"], b["y2"])
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        inter = (ix2 - ix1) * (iy2 - iy1)
        area_a = (a["x2"] - a["x1"]) * (a["y2"] - a["y1"])
        area_b = (b["x2"] - b["x1"]) * (b["y2"] - b["y1"])
        union = area_a + area_b - inter
        return inter / union if union > 0 else 0.0

    def merge_results(self, easy: list, doctr: list) -> list:
        """Combine both engine outputs; keep the higher-confidence word per region."""
        all_words = easy + doctr
        if not all_words:
            return []
        used = set()
        merged = []
        all_words.sort(key=lambda w: (round(w["y1"] / 12) * 12, w["x1"]))
        for i, w1 in enumerate(all_words):
            if i in used:
                continue
            best = w1
            for j, w2 in enumerate(all_words):
                if j == i or j in used:
                    continue
                if self._iou(w1, w2) > 0.35:
                    used.add(j)
                    if w2["conf"] > best["conf"]:
                        best = w2
            merged.append(best)
            used.add(i)
        return merged

    # ------------------------------------------------------------------ #
    #  Public entry point                                                  #
    # ------------------------------------------------------------------ #

    def process_file(self, file_bytes: bytes, content_type: str) -> list:
        """Return merged word-dicts (with absolute y positions across pages)."""
        if "pdf" in content_type.lower() or content_type == "application/octet-stream":
            try:
                images = self.pdf_to_images(file_bytes)
            except Exception:
                img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
                images = [img]
        else:
            img = Image.open(io.BytesIO(file_bytes)).convert("RGB")
            images = [img]

        all_words = []
        page_offset = 0.0

        for img in images:
            _, img_h = img.size
            easy_res = self.run_easyocr(img)
            doctr_res = self.run_doctr(img)
            merged = self.merge_results(easy_res, doctr_res)

            for w in merged:
                w["y1"] += page_offset
                w["y2"] += page_offset

            all_words.extend(merged)
            page_offset += float(img_h) + 80.0   # gap between pages

        return all_words
