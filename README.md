# Bank Statement Extractor – AI Bank Statement Extractor

BankLens is an AI-powered tool for extracting structured transaction data from bank statement PDFs or images. It combines dual OCR engines (EasyOCR + DocTR), YOLOv8 table detection, and advanced parsing logic to deliver clean, exportable transaction data in JSON, CSV, or Excel formats.

---

## Features

- **Dual OCR Engines:** Uses EasyOCR and DocTR for robust text extraction from scanned documents.
- **YOLOv8 Table Detection:** Identifies tabular regions in statements for accurate parsing.
- **Flexible Input:** Supports PDF, PNG, JPG, TIFF, BMP, and WEBP files (up to 25 MB).
- **Modern UI:** Beautiful dark-themed web frontend (BankLens) and a Streamlit app for quick local use.
- **Export Options:** Download extracted transactions as JSON, CSV, or Excel.
- **API-Driven:** RESTful Flask backend with endpoints for upload, status, and download.

---

## Project Structure

```
app.py                # Flask backend (API, OCR, parsing, export)
ocr_engine.py         # OCR engine abstraction (EasyOCR + DocTR)
parser.py             # Logic to convert OCR output to transactions
streamlit_app.py      # Streamlit UI for local/quick use
requirements.txt      # Python dependencies
static/
  css/style.css       # Frontend styles (dark mode, modern UI)
  js/main.js          # Frontend logic (upload, polling, table, export)
templates/
  index.html          # Main HTML template for Flask frontend
```

---

## Installation

1. **Clone the repository:**
   ```sh
   git clone <repo-url>
   cd bank_statement_extractor
   ```

2. **Create and activate a virtual environment:**
   ```sh
   python -m venv .venv
   .venv\Scripts\activate  # On Windows
   # Or: source .venv/bin/activate  # On macOS/Linux
   ```

3. **Install dependencies:**
   ```sh
   pip install -r requirements.txt
   ```

---

## Usage

### 1. Flask Web App (API + Frontend)

- **Start the Flask server:**
  ```sh
  python app.py
  ```
- Open your browser at [http://localhost:5000](http://localhost:5000)
- Upload a bank statement PDF or image, and extract transactions instantly.

#### API Endpoints
- `POST /api/upload` – Upload a file, returns a `task_id`.
- `GET /api/status/<task_id>` – Poll for processing status and results.
- `GET /api/download/<task_id>/<fmt>` – Download results as `json` or `csv`.

### 2. Streamlit App (Quick Local Extraction)

- **Start the Streamlit app:**
  ```sh
  streamlit run streamlit_app.py
  ```
- Use the web UI to upload files and download extracted data.

---

## How It Works

1. **Upload:** User uploads a PDF/image via the web UI or API.
2. **OCR:** The backend runs EasyOCR and DocTR to extract all text.
3. **Parsing:** The parser groups words into rows, detects headers, infers columns, and extracts transactions (date, description, debit, credit, balance).
4. **Export:** Results are available for download in JSON, CSV, or Excel formats.

---

## Customization & Notes

- **YOLOv8 Model:** For best table detection, train a YOLO model on your own statement samples and update the model path in `streamlit_app.py`.
- **OCR Engines:** Both EasyOCR and DocTR are used; DocTR is optional and will be skipped if unavailable.
- **API Key:** The Streamlit app uses OpenRouter's DeepSeek model for advanced parsing. Replace the API key in `streamlit_app.py` with your own for production use.

---

## License

MIT License. See `LICENSE` file for details.

---

## Credits
- [EasyOCR](https://github.com/JaidedAI/EasyOCR)
- [DocTR](https://github.com/mindee/doctr)
- [Ultralytics YOLOv8](https://github.com/ultralytics/ultralytics)
- [Streamlit](https://streamlit.io/)
- [Flask](https://flask.palletsprojects.com/)

---

## Screenshots

![screenshot](static/screenshot.png) <!-- Add your own screenshot here -->
