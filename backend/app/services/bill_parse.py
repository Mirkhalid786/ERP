"""
Parse text lines read from an Indian pharmacy purchase bill into header fields, line items and totals.

Every value is reported with a confidence ("high", "medium", "low") and nothing is invented: a value that cannot be
found is None. Line items are read using the column order of the bill's own header row (HSN, product, pack, batch,
expiry, qty, free, MRP, rate, discount, GST, amount) when one is found, and are cross-checked arithmetically
(qty × rate less discount ≈ amount). The text is untrusted input: it is only matched against patterns.
"""
import calendar
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

GSTIN_RE = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b")
AMOUNT = r"-?\d{1,3}(?:,\d{2,3})+(?:\.\d{1,2})?|-?\d+(?:\.\d{1,2})?"
DATE_PATTERNS = [
    (re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})\b"), "dmy"),
    (re.compile(r"\b(\d{4})[/\-.](\d{1,2})[/\-.](\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2})\b"), "dmy2"),
    (re.compile(r"\b(\d{1,2})[\s\-/]*(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s\-/,]*(\d{2,4})\b", re.I), "dmony"),
]
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}
EXPIRY_RE = re.compile(r"^(0?[1-9]|1[0-2])[/\-](\d{2}|\d{4})$")      # "08/27", "8-2027" (not "6.19": an amount)
EXPIRY_MON_RE = re.compile(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\-/']?(\d{2}|\d{4})$", re.I)
PACK_RE = re.compile(r"^(\d+(?:x\d+)?)\s*('?s|s|x|tab|tabs|cap|caps|ml|gm|g|nos|n)?$", re.I)
HSN_RE = re.compile(r"^(30|90|40|38|33|34|21|48|56|63)\d{2,6}$")
GST_RATES = {Decimal("0"), Decimal("5"), Decimal("12"), Decimal("18"), Decimal("28")}

HEADER_WORDS = {
    "hsn": ["hsn"], "product": ["product", "description", "particulars", "item", "medicine", "name of"],
    "pack": ["pack", "packing", "pkg"], "batch": ["batch", "b.no", "bno"], "expiry": ["exp", "expiry"],
    "qty": ["qty", "quantity"], "free": ["free", "fr.", "sch"], "mrp": ["mrp"],
    "rate": ["rate", "ptr", "pts", "price"], "discount": ["disc", "dis%", "dis.", "cd%"], "gst": ["gst", "tax%"],
    "amount": ["amount", "amt", "net value", "value", "total"],
}
NUMERIC_COLS = ["qty", "free", "mrp", "rate", "discount", "gst", "amount"]
TOTAL_LINE = re.compile(r"\b(sub\s*total|taxable|grand\s*total|net\s*(amount|payable|value)|total\s*amount|round\s*off|cgst|sgst|igst|amount\s*in\s*words|rupees)\b", re.I)


def F(value: Any = None, confidence: str = "low", source: str = "") -> Dict[str, Any]:
    return {"value": value, "confidence": confidence if value is not None else "low", "source": source[:160]}


def dec(text: str) -> Optional[Decimal]:
    try:
        return Decimal(text.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def parse_date(text: str) -> Optional[date]:
    for pat, kind in DATE_PATTERNS:
        m = pat.search(text)
        if not m:
            continue
        try:
            if kind == "dmy":
                d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif kind == "ymd":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            elif kind == "dmy2":
                d, mo, y = int(m.group(1)), int(m.group(2)), 2000 + int(m.group(3))
            else:
                d, mo, y = int(m.group(1)), MONTHS[m.group(2).lower()[:3]], int(m.group(3))
                y = y + 2000 if y < 100 else y
            return date(y, mo, d)
        except (ValueError, KeyError):
            continue
    return None


def parse_expiry(token: str) -> Optional[date]:
    """'08/27', '8-2027', 'AUG-27' -> last day of that month (medicine expiry runs to the end of the month)."""
    t = token.strip().rstrip(".,")
    m = EXPIRY_RE.match(t)
    if m:
        mo, y = int(m.group(1)), int(m.group(2))
    else:
        m = EXPIRY_MON_RE.match(t)
        if not m:
            return None
        mo, y = MONTHS[m.group(1).lower()[:3]], int(m.group(2))
    y = y + 2000 if y < 100 else y
    if not 2000 <= y <= 2100:
        return None
    return date(y, mo, calendar.monthrange(y, mo)[1])


def _labelled_amount(lines: List[str], label: str, last: bool = True, require_decimal: bool = True) -> Dict[str, Any]:
    pat = re.compile(label + r"[^0-9\-]{0,25}?(" + AMOUNT + r")", re.I)
    found = None
    for ln in lines:
        for m in pat.finditer(ln):
            raw = m.group(1)
            if require_decimal and "." not in raw:
                continue
            found = (raw, ln)
            if not last:
                break
        if found and not last:
            break
    if not found:
        return F()
    return F(str(dec(found[0])), "high", found[1])


def _labelled_text(lines: List[str], pattern: str) -> Dict[str, Any]:
    pat = re.compile(pattern, re.I)
    for ln in lines:
        m = pat.search(ln)
        if m:
            return F(m.group(1).strip().rstrip(".,:"), "high", ln)
    return F()


def _labelled_date(lines: List[str], label: str) -> Dict[str, Any]:
    pat = re.compile(label + r"\s*[:\-]?\s*(.{6,20})", re.I)
    for ln in lines:
        m = pat.search(ln)
        if m:
            d = parse_date(m.group(1))
            if d:
                return F(d.isoformat(), "high", ln)
    return F()


def _first_date(lines: List[str]) -> Dict[str, Any]:
    """The invoice date: labelled "Invoice/Bill Date" first, then "Date"/"Dated"."""
    found = _labelled_date(lines, r"\b(?:invoice|inv|bill)\s*date")
    return found if found["value"] else _labelled_date(lines, r"\bdated?\b")


def _supplier_name(lines: List[str]) -> Dict[str, Any]:
    skip = re.compile(r"(tax\s*invoice|invoice|gstin|bill|original|duplicate|triplicate|phone|ph[:.]|mob|email|e-mail|"
                      r"d\.?l\.?\s*no|dl\s*no|cash|credit|memo|page|state|address|www\.|@)", re.I)
    for ln in lines[:6]:
        clean = ln.strip()
        if len(re.findall(r"[A-Za-z]{2,}", clean)) >= 2 and not skip.search(clean) and len(clean) <= 80:
            return F(re.sub(r"\s{2,}", " ", clean), "medium", ln)
    return F()


def _header_layout(lines: List[str]) -> Tuple[Optional[int], List[str]]:
    """Index of the item-table header row and the order of its columns."""
    best, best_cols = None, []
    for idx, ln in enumerate(lines):
        low = ln.lower()
        positions = {}
        for col, words in HEADER_WORDS.items():
            for w in words:
                p = low.find(w)
                if p >= 0 and (col not in positions or p < positions[col]):
                    if col == "amount" and w == "total" and "total" in low and positions.get("amount") is not None:
                        continue
                    positions[col] = p
        if "rate" in positions and "mrp" in positions and positions["rate"] == positions["mrp"]:
            positions.pop("rate")
        score = len(positions)
        if score >= 5 and ("batch" in positions or "qty" in positions) and score > len(best_cols):
            best, best_cols = idx, [c for c, _ in sorted(positions.items(), key=lambda kv: kv[1])]
    return best, best_cols


def _is_number(tok: str) -> bool:
    return re.fullmatch(r"-?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|-?\d+(?:\.\d+)?%?", tok) is not None


def _check(qty, free, rate, disc, gst, amount) -> bool:
    if None in (qty, rate, amount) or qty <= 0:
        return False
    zero, hundred = Decimal("0"), Decimal("100")
    base = qty * rate * (1 - (disc or zero) / hundred)
    for total in (base, base * (1 + (gst or zero) / hundred)):
        if total and abs(total - amount) <= max(Decimal("0.05") * amount, Decimal("1")):
            return True
    return False


def _parse_item(line: str, cols: List[str]) -> Optional[Dict[str, Any]]:
    tokens = line.split()
    exp_idx = next((i for i, t in enumerate(tokens) if parse_expiry(t)), None)
    if exp_idx is None:
        return None
    item: Dict[str, Any] = {"raw": line[:300], "issues": []}
    expiry = parse_expiry(tokens[exp_idx])
    item["expiry"] = F(expiry.isoformat(), "high", tokens[exp_idx])
    before, after = tokens[:exp_idx], tokens[exp_idx + 1:]
    hsn = None
    if before and HSN_RE.match(before[0]):
        hsn = before.pop(0)
    item["hsn"] = F(hsn, "high" if hsn else "low")
    batch = None
    if before and re.search(r"\d", before[-1]) and not PACK_RE.match(before[-1]) and len(before[-1]) >= 3:
        batch = before.pop()
    item["batch"] = F(batch.upper() if batch else None, "medium" if batch else "low", batch or "")
    pack = None
    if before and PACK_RE.match(before[-1]) and len(before) > 1:
        pack = before.pop()
    item["pack"] = F(pack, "medium" if pack else "low")
    pack_size = None
    if pack:
        m = re.match(r"(\d+)(?:x(\d+))?", pack, re.I)
        pack_size = int(m.group(1)) * (int(m.group(2)) if m.group(2) else 1)
    item["pack_size"] = F(pack_size, "medium" if pack_size else "low")
    desc = " ".join(before).strip(" -|")
    item["description"] = F(desc or None, "medium" if desc else "low", desc)

    nums = [dec(t.rstrip("%")) for t in after if _is_number(t)]
    nums = [n for n in nums if n is not None]
    numeric_order = [c for c in cols if c in NUMERIC_COLS]
    values: Dict[str, Optional[Decimal]] = {c: None for c in NUMERIC_COLS}
    mapped = False
    if numeric_order and len(nums) == len(numeric_order):
        values.update(dict(zip(numeric_order, nums)))
        mapped = True
    elif numeric_order and "free" in numeric_order and len(nums) == len(numeric_order) - 1:
        order = [c for c in numeric_order if c != "free"]                      # free column left blank
        values.update(dict(zip(order, nums)))
        values["free"] = Decimal("0")
        mapped = True
    if not mapped and nums:
        # No usable header: amount is the last number, GST the rate-like value before it, qty the first whole number
        rest = list(nums)
        values["amount"] = rest.pop()
        gi = next((i for i in range(len(rest) - 1, -1, -1) if rest[i] in GST_RATES), None)
        if gi is not None:
            values["gst"] = rest.pop(gi)
        qi = next((i for i, n in enumerate(rest) if n == n.to_integral_value() and n > 0), None)
        if qi is not None:
            values["qty"] = rest.pop(qi)
            if rest and rest[0] == rest[0].to_integral_value() and rest[0] < values["qty"]:
                values["free"] = rest.pop(0)
        decimals = sorted([n for n in rest if n > 0], reverse=True)
        if len(decimals) >= 2:
            values["mrp"], values["rate"] = decimals[0], decimals[1]
            small = [n for n in rest if n not in (decimals[0], decimals[1]) and n <= 100]
            values["discount"] = small[0] if small else None
        elif decimals:
            values["rate"] = decimals[0]
    consistent = _check(values["qty"], values["free"], values["rate"], values["discount"], values["gst"], values["amount"])
    conf = "high" if mapped and consistent else "medium" if consistent or mapped else "low"
    for c in NUMERIC_COLS:
        v = values[c]
        item[c] = F(str(v) if v is not None else None, conf, "")
    if values["gst"] is not None and values["gst"] not in GST_RATES:
        item["issues"].append(f"GST {values['gst']}% is not a standard rate")
        item["gst"]["confidence"] = "low"
    if not consistent:
        item["issues"].append("Quantity × rate less discount does not match the line amount — check the numbers")
    if not batch:
        item["issues"].append("Batch number not found")
    return item


def parse_bill(lines: List[str], own_gstin: Optional[str] = None) -> Dict[str, Any]:
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in lines if ln and ln.strip()]
    text = "\n".join(lines)
    gstins = [g for g in dict.fromkeys(GSTIN_RE.findall(text.upper())) if g != (own_gstin or "").upper()]
    supplier_gstin = F(gstins[0], "high" if len(gstins) == 1 else "medium", gstins[0]) if gstins else F()
    out: Dict[str, Any] = {
        "supplier": {
            "name": _supplier_name(lines),
            "gstin": supplier_gstin,
            "drug_licence": _labelled_text(lines, r"\bd\.?\s?l\.?\s*(?:no|number)?\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9/,\- ]{4,60})"),
        },
        "invoice": {
            "number": _labelled_text(lines, r"\b(?:invoice|inv|bill)\s*(?:no|number|#)\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-_.]{0,39})"),
            "date": _first_date(lines),
            "due_date": _labelled_date(lines, r"\bdue\s*date"),
            "po_reference": _labelled_text(lines, r"\b(?:p\.?\s?o\.?|order)\s*(?:no|number|ref)\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9/\-_.]{0,39})"),
        },
        "totals": {
            "taxable": _labelled_amount(lines, r"\btaxable\s*(?:amount|amt|value|val)?"),
            "cgst": _labelled_amount(lines, r"\bcgst(?:\s*amt|\s*amount)?(?!\s*%)"),
            "sgst": _labelled_amount(lines, r"\bsgst(?:\s*amt|\s*amount)?(?!\s*%)"),
            "igst": _labelled_amount(lines, r"\bigst(?:\s*amt|\s*amount)?(?!\s*%)"),
            "round_off": _labelled_amount(lines, r"\bround\s*(?:off|ing)?", require_decimal=False),
            "grand_total": _labelled_amount(lines, r"\b(?:grand\s*total|net\s*(?:amount|payable|value)|invoice\s*total|bill\s*amount|total\s*amount)"),
        },
        "items": [],
        "warnings": [],
    }
    header_idx, cols = _header_layout(lines)
    start = header_idx + 1 if header_idx is not None else 0
    if header_idx is None:
        out["warnings"].append("No item table header was recognised; line items are a best guess")
    for ln in lines[start:]:
        if TOTAL_LINE.search(ln):            # the totals section ends the item table
            if out["items"]:
                break
            continue
        item = _parse_item(ln, cols)
        if item:
            out["items"].append(item)
    if not out["items"]:
        out["warnings"].append("No line items were recognised; add them on the review screen")
    out["columns"] = cols
    return out
