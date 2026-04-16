"""
parser.py – Convert raw OCR word-dicts into structured transaction records.

Pipeline
────────
1. Group words into visual rows (y-coordinate clustering).
2. Detect the header row (keywords: Date, Description, Debit, Credit, Balance).
3. Infer column x-positions from the header.
4. For each data row:
   a. Extract date (first 2-3 words).
   b. Classify remaining tokens as amounts or description text.
   c. Map amounts to columns by nearest x-position.
5. Merge multi-line description continuations.
6. Normalise output: dates → YYYY-MM-DD, amounts → float | None.
"""

import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple


# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

HEADER_KEYWORDS = {
    "date", "description", "narration", "particulars", "details",
    "debit", "credit", "withdrawal", "deposit", "balance", "amount",
    "dr", "cr", "cheque", "ref", "remarks", "withdrawals", "deposits",
}

SKIP_KEYWORDS = [
    "page", "statement of", "account number", "account no", "branch",
    "ifsc", "opening balance", "closing balance", "brought forward",
    "carried forward", "total", "sub total",
]

DEBIT_HINTS = {"debit", "withdrawal", "payment", "paid", "purchase", "fee",
               "charge", "emi", "dr", "atm"}
CREDIT_HINTS = {"credit", "deposit", "salary", "refund", "reversal",
                "interest", "credit", "cr", "received"}

AMOUNT_RE = re.compile(r"^[₹$€£¥(]?-?\d{1,3}(?:[,\d]{3})*(?:\.\d{0,2})?[)]?$")
CURRENCY_STRIP = re.compile(r"[₹$€£¥,\s]")


# ─────────────────────────────────────────────────────────────────────────────
#  Date helpers
# ─────────────────────────────────────────────────────────────────────────────

def _try_date(d: int, m: int, y: int) -> Optional[str]:
    if y < 100:
        y += 2000
    try:
        return datetime(y, m, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_date(text: str) -> Optional[str]:
    """Return YYYY-MM-DD or None."""
    t = text.strip().rstrip(".,")

    # DD/MM/YYYY  or  YYYY/MM/DD  (separator: / - . space)
    m = re.search(r"(\d{1,2})[/\-.\s]+(\d{1,2})[/\-.\s]+(\d{2,4})", t)
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 31:        # YYYY-MM-DD
            return _try_date(c, b, a)
        return _try_date(a, b, c)

    # DD Mon YYYY  /  DD-Mon-YY
    m = re.search(
        r"(\d{1,2})[/\-\s]+(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[/\-\s]+(\d{2,4})",
        t, re.IGNORECASE,
    )
    if m:
        return _try_date(
            int(m.group(1)),
            MONTH_MAP[m.group(2).lower()],
            int(m.group(3)),
        )

    return None


def try_two_token_date(tok1: str, tok2: str) -> Optional[str]:
    """Try joining two adjacent tokens as a date (e.g. '01 Jan' + '2024')."""
    return parse_date(f"{tok1} {tok2}")


# ─────────────────────────────────────────────────────────────────────────────
#  Amount helpers
# ─────────────────────────────────────────────────────────────────────────────

def is_amount(text: str) -> bool:
    t = text.strip().upper()
    if t.endswith(" CR") or t.endswith(" DR"):
        t = t[:-3]
    elif t.endswith("CR") or t.endswith("DR"):
        t = t[:-2]
        
    digits = sum(c.isdigit() for c in t)
    if digits == 0: return False
    
    valid_chars = sum(c.isdigit() or c in ".,-+()₹$€£¥ " for c in t)
    return (valid_chars / len(t)) >= 0.7 and digits > 0

def parse_amount(text: str) -> Optional[float]:
    t = text.strip().upper()
    negative = "-" in t or ("(" in t and ")" in t) or t.endswith(" DR") or t.endswith("DR")
    
    nums = re.sub(r"[^\d.]", "", t)
    if not nums: return None
    
    if nums.count(".") > 1:
        parts = nums.split(".")
        nums = "".join(parts[:-1]) + "." + parts[-1]
        
    try:
        v = float(nums)
        return -v if negative else v
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
#  Row grouping
# ─────────────────────────────────────────────────────────────────────────────

def group_into_rows(words: List[Dict], threshold: float = 25.0) -> List[List[Dict]]:
    """Cluster words into rows by y1 proximity."""
    if not words:
        return []
    words_s = sorted(words, key=lambda w: w["y1"])
    rows: List[List[Dict]] = []
    current = [words_s[0]]
    cur_y = words_s[0]["y1"]
    for w in words_s[1:]:
        if abs(w["y1"] - cur_y) <= threshold:
            current.append(w)
        else:
            rows.append(sorted(current, key=lambda x: x["x1"]))
            current = [w]
            cur_y = w["y1"]
    if current:
        rows.append(sorted(current, key=lambda x: x["x1"]))
    return rows


def row_text(row: List[Dict]) -> str:
    return " ".join(w["text"] for w in row)


# ─────────────────────────────────────────────────────────────────────────────
#  Header detection & column inference
# ─────────────────────────────────────────────────────────────────────────────

def detect_header(rows: List[List[Dict]]) -> Optional[int]:
    for i, row in enumerate(rows[:30]):          # only check first 30 rows
        rt = row_text(row).lower()
        hits = sum(1 for kw in HEADER_KEYWORDS if kw in rt)
        if hits >= 2:
            return i
    return None


_COL_SYNONYMS: Dict[str, List[str]] = {
    "date":        ["date", "value date", "txn date", "transaction date", "posting"],
    "description": ["description", "narration", "particulars", "details",
                    "remarks", "transaction", "particular"],
    "debit":       ["debit", "withdrawal", "withdrawals", "dr", "debit amount"],
    "credit":      ["credit", "deposit", "deposits", "cr", "credit amount"],
    "balance":     ["balance", "bal", "running balance", "closing"],
}


def infer_columns(header_row: List[Dict]) -> Dict[str, float]:
    cols: Dict[str, float] = {}
    for word in header_row:
        wl = word["text"].lower()
        for col, syns in _COL_SYNONYMS.items():
            if any(s in wl for s in syns) and col not in cols:
                cols[col] = (word["x1"] + word["x2"]) / 2.0
                break
    return cols


def nearest_column(x: float, cols: Dict[str, float], max_dist: float = 250.0) -> Optional[str]:
    if not cols:
        return None
    nearest, dist = min(cols.items(), key=lambda c: abs(c[1] - x))
    return nearest if dist <= max_dist else None


# ─────────────────────────────────────────────────────────────────────────────
#  Main parser
# ─────────────────────────────────────────────────────────────────────────────

def _should_skip(rt: str) -> bool:
    rt_l = rt.lower()
    for kw in SKIP_KEYWORDS:
        if kw in rt_l:
            # Only skip if the row has no amounts (i.e. it's a pure label row)
            if not any(c.isdigit() for c in rt):
                return True
    return False


def _classify_by_hint(description: str) -> Tuple[bool, bool]:
    """Return (is_debit_hint, is_credit_hint) from description keywords."""
    dl = description.lower()
    is_debit = any(h in dl for h in DEBIT_HINTS)
    is_credit = any(h in dl for h in CREDIT_HINTS)
    return is_debit, is_credit


def parse_transactions(words: List[Dict]) -> List[Dict]:
    """Main entry point – returns list of transaction dicts."""
    if not words:
        return []

    rows = group_into_rows(words)
    header_idx = detect_header(rows)
    cols: Dict[str, float] = {}

    if header_idx is not None:
        cols = infer_columns(rows[header_idx])
        data_rows = rows[header_idx + 1:]
    else:
        data_rows = rows

    transactions: List[Dict] = []
    current: Optional[Dict] = None

    for row in data_rows:
        rt = row_text(row)
        if not rt.strip():
            continue
        if _should_skip(rt):
            continue

        # ── Attempt date extraction from first 5 tokens ──────────────────
        date_val: Optional[str] = None
        date_token_indices: List[int] = []

        # Try to find a date in the first up to 5 tokens to be safe
        for i, word in enumerate(row[:5]):
            d = parse_date(word["text"])
            if d:
                date_val = d
                date_token_indices = [i]
                break
            # Two-token date (e.g. "01 Jan" + "2024")
            if i < len(row) - 1:
                d2 = try_two_token_date(word["text"], row[i + 1]["text"])
                if d2:
                    date_val = d2
                    date_token_indices = [i, i+1]
                    break
            # Three-token date (e.g. "01" + "Jan" + "2024")
            if i < len(row) - 2:
                d3 = parse_date(f"{word['text']} {row[i+1]['text']} {row[i+2]['text']}")
                if d3:
                    date_val = d3
                    date_token_indices = [i, i+1, i+2]
                    break

        # ── Classify each token ───────────────────────────────────────────
        amounts: List[Dict] = []
        desc_tokens: List[str] = []

        for i, word in enumerate(row):
            if i in date_token_indices:
                continue
            txt = word["text"]
            if is_amount(txt) and parse_date(txt) is None:
                parsed = parse_amount(txt)
                if parsed is not None:
                    amounts.append({
                        "text": txt,
                        "value": parsed,
                        "x": (word["x1"] + word["x2"]) / 2.0,
                    })
            else:
                # Don't add the date token again
                if parse_date(txt) is None:
                    desc_tokens.append(txt)

        description = re.sub(r"\s+", " ", " ".join(desc_tokens)).strip()

        # ── Map amounts to columns ────────────────────────────────────────
        debit: Optional[float] = None
        credit: Optional[float] = None
        balance: Optional[float] = None

        if amounts and cols:
            for amt in amounts:
                col = nearest_column(amt["x"], cols)
                if col == "debit" and debit is None:
                    debit = amt["value"]
                elif col == "credit" and credit is None:
                    credit = amt["value"]
                elif col == "balance" and balance is None:
                    balance = amt["value"]
                elif col == "date":
                    pass   # misfire, ignore
        elif amounts:
            # Heuristic: rightmost = balance, second-right = debit or credit
            srt = sorted(amounts, key=lambda a: a["x"])
            if len(srt) == 1:
                balance = srt[0]["value"]
            elif len(srt) == 2:
                debit = srt[0]["value"]
                balance = srt[1]["value"]
            else:
                debit = srt[0]["value"]
                credit = srt[1]["value"]
                balance = srt[-1]["value"]

        # ── Build or extend transaction ───────────────────────────────────
        if date_val:
            if current is not None:
                transactions.append(current)
            current = {
                "date": date_val,
                "description": description,
                "debit": debit,
                "credit": credit,
                "balance": balance,
            }
        elif current is not None:
            if description and not amounts:
                # Multi-line description
                current["description"] = (current["description"] + " " + description).strip()
            else:
                # Fill in missing amounts
                if current["debit"] is None and debit is not None:
                    current["debit"] = debit
                if current["credit"] is None and credit is not None:
                    current["credit"] = credit
                if current["balance"] is None and balance is not None:
                    current["balance"] = balance

    if current is not None:
        transactions.append(current)

    # ── Post-processing ───────────────────────────────────────────────────

    cleaned: List[Dict] = []
    for tx in transactions:
        if not tx.get("date"):
            continue

        # Zero-amount cleanup
        if tx["debit"] == 0.0:
            tx["debit"] = None
        if tx["credit"] == 0.0:
            tx["credit"] = None

        # Keyword-based classification when both are None but an amount exists
        if tx["debit"] is None and tx["credit"] is None and tx["balance"] is not None:
            # Can't reliably split; leave as-is
            pass
        elif tx["debit"] is not None and tx["credit"] is not None:
            # Both filled – if one looks like it should be balance, correct it
            pass

        # Use description hints if still unclassified
        if tx["debit"] is None and tx["credit"] is None:
            is_d, is_c = _classify_by_hint(tx["description"])
            if is_d and not is_c:
                tx["debit"] = tx["balance"]
                tx["balance"] = None
            elif is_c and not is_d:
                tx["credit"] = tx["balance"]
                tx["balance"] = None

        cleaned.append(tx)

    return cleaned
