import streamlit as st
import pandas as pd
import io
from PIL import Image
import numpy as np

from ultralytics import YOLO

# Import local processing modules
from ocr_engine import OCREngine
from parser import group_into_rows, row_text
import requests
import json

st.set_page_config(page_title="Bank Statement Extractor", layout="wide", page_icon="🏦")

@st.cache_resource
def load_yolo():
    # Load YOLOv8 model. By default this downloads the nano model 'yolov8n.pt'
    # To detect bank statement tables accurately, you should train a YOLO model 
    # on document tables and supply its path here instead (e.g. 'table_detector.pt').
    model = YOLO("yolov8n.pt") 
    return model

@st.cache_resource
def load_ocr():
    # Load OCR engine (initializes once)
    return OCREngine(use_gpu=False)

st.title("Bank Statement Extractor")
st.markdown("Extract structured transactions from PDFs or Images using **Streamlit**, **YOLOv8** region detection, and **EasyOCR/DocTR** dual-engine OCR.")

yolo_model = load_yolo()
ocr_engine = load_ocr()

uploaded_file = st.file_uploader("Upload Bank Statement (PDF, PNG, JPG)", type=["pdf", "png", "jpg", "jpeg"])

if uploaded_file is not None:
    file_bytes = uploaded_file.read()
    content_type = uploaded_file.type

    st.info("Parsing document...")
    
    # 1. Convert document to workable images
    try:
        if "pdf" in content_type.lower():
            images = ocr_engine.pdf_to_images(file_bytes)
        else:
            images = [Image.open(io.BytesIO(file_bytes)).convert("RGB")]
    except Exception as e:
        st.error(f"Failed to read file: {e}")
        st.stop()

    all_words = []
    page_offset = 0.0

    st.subheader(f"Extracted {len(images)} Page(s)")

    # Create layout columns to show images side-by-side with data
    col1, col2 = st.columns([1, 2])

    for i, img in enumerate(images):
        with col1:
            st.markdown(f"**Page {i + 1} - YOLO Detections**")
            # 2. Run YOLO inference to detect specific regions (tables/elements)
            yolo_results = yolo_model(img, verbose=False)
            
            # Plot YOLO's bounding boxes over the image
            res_plotted = yolo_results[0].plot()
            st.image(res_plotted, use_container_width=True)
            
            st.caption("YOLO identified the bounding boxes above. For optimal table detection, you'll need a YOLO model fine-tuned on tabular documents.")

        with col2:
            with st.spinner(f"Running OCR on Page {i + 1}..."):
                # 3. Run dual OCR engines on the image
                easy_res = ocr_engine.run_easyocr(img)
                doctr_res = ocr_engine.run_doctr(img)
                merged = ocr_engine.merge_results(easy_res, doctr_res)

                # Offset y-coordinates for documents with multiple pages
                _, img_h = img.size
                for w in merged:
                    w["y1"] += page_offset
                    w["y2"] += page_offset

                all_words.extend(merged)
                page_offset += float(img_h) + 80.0

    if all_words:
        with st.spinner("Applying DeepSeek AI parsing algorithms via OpenRouter..."):
            transactions = []
            try:
                # Group words into text lines to provide structure for the LLM
                rows = group_into_rows(all_words)
                full_text = "\n".join([row_text(r) for r in rows])
                
                # Call OpenRouter API using deepseek-chat
                api_key = "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"
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
                
                system_prompt = (
                    "You are a specialized financial data extraction AI. Extract all tabular transactions from the provided bank statement OCR text. "
                    "Return ONLY a raw JSON array of objects. Do not use Markdown formatting or include ```json. "
                    "The JSON objects MUST have exactly these keys: date (format: YYYY-MM-DD), description (string), debit (number or null), credit (number or null), balance (number or null)."
                )
                
                payload = {
                    "model": "deepseek/deepseek-chat",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": full_text}
                    ]
                }
                
                resp = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload)
                resp.raise_for_status()
                
                answer = resp.json()["choices"][0]["message"]["content"].strip()
                
                # Cleanup if the model returned markdown
                if answer.startswith("```json"):
                    answer = answer[7:]
                if answer.startswith("```"):
                    answer = answer[3:]
                if answer.endswith("```"):
                    answer = answer[:-3]
                    
                transactions = json.loads(answer.strip())
                
            except Exception as e:
                st.error(f"DeepSeek Evaluation Failed: {e}")

        st.divider()
        st.subheader("📋 Extracted Transactions")

        if transactions:
            df = pd.DataFrame(transactions)
            
            # Reorder columns standardly
            cols = [c for c in ["date", "description", "debit", "credit", "balance"] if c in df.columns]
            df = df[cols]

            st.success(f"Successfully extracted {len(df)} transactions!")
            st.dataframe(df, use_container_width=True)

            # --- Export functional buttons
            col_d1, col_d2, col_d3 = st.columns(3)
            with col_d1:
                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Download CSV",
                    data=csv,
                    file_name='extracted_transactions.csv',
                    mime='text/csv',
                    use_container_width=True
                )
            with col_d2:
                json_str = df.to_json(orient="records", indent=2)
                st.download_button(
                    label="📥 Download JSON",
                    data=json_str,
                    file_name='extracted_transactions.json',
                    mime='application/json',
                    use_container_width=True
                )
            with col_d3:
                import io
                excel_buffer = io.BytesIO()
                df.to_excel(excel_buffer, index=False, engine='openpyxl')
                excel_buffer.seek(0)
                st.download_button(
                    label="📥 Download Excel",
                    data=excel_buffer,
                    file_name='extracted_transactions.xlsx',
                    mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    use_container_width=True
                )
        else:
            st.warning("OCR executed successfully, however the parser could not identify formal table headers/structure to build transactions. Try a clearer document.")
    else:
        st.error("No text could be extracted from this document.")