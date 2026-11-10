import csv
import re
from collections import Counter

CSV_PATH = "/app/data/catalogue/indian_medicine_data.csv"

# Dosage forms patterns
DOSAGE_PATTERNS = [
    (r"\b(tablet\s+sr|tablet\s+er|tablet\s+pr|tablet\s+cr|tablet\s+mr|tablet\s+dr|tablet\s+dt|tablet\s+md|tablets?|tabs?)\b", "Tablet"),
    (r"\b(capsule\s+sr|capsule\s+er|capsule\s+dr|capsules?|caps?)\b", "Capsule"),
    (r"\b(dry\s+syrup|syrup|syp|syr)\b", "Syrup"),
    (r"\b(oral\s+suspension|suspension|susp)\b", "Suspension"),
    (r"\b(powder\s+for\s+injection|injections?|inj)\b", "Injection"),
    (r"\b(eye\s+drops?|ear\s+drops?|nasal\s+drops?|drops?)\b", "Drops"),
    (r"\b(eye\s+ointment|ointment|oint)\b", "Ointment"),
    (r"\b(cream)\b", "Cream"),
    (r"\b(gel)\b", "Gel"),
    (r"\b(lotion)\b", "Lotion"),
    (r"\b(inhaler|rotacaps?|respules?)\b", "Inhaler"),
    (r"\b(nasal\s+spray|spray)\b", "Spray"),
    (r"\b(sachets?)\b", "Sachet"),
    (r"\b(powder)\b", "Powder"),
    (r"\b(infusion|solution|liquid)\b", "Solution"),
    (r"\b(mouthwash|gargle)\b", "Liquid"),
]

PACK_REGEX = re.compile(
    r"(?i)^(strip|box|bottle|tube|vial|ampoule|packet|blister|pack|jar|sachet|can|carton|tin|prefilled syringe|pre-filled syringe|syringe)\s+of\s+([0-9.]+)\s*(.*)$"
)

# Extract strength from name, e.g., "Dolo 650", "Augmentin 625 Duo", "Azithral 500mg"
STRENGTH_REGEX = re.compile(
    r"(?i)\b(\d+(?:\.\d+)?\s*(?:mg|mcg|gm|g|ml|%|iu|duo|forte|ds|sr|er|pr|mr|cr|dt|md|xr)?(?:\s*/\s*\d+(?:\.\d+)?\s*(?:mg|mcg|gm|g|ml)?)?)\b"
)

def test_parsing():
    total = 0
    parsed_forms = Counter()
    unparsed_forms = Counter()
    parsed_packs = 0
    unparsed_packs = 0
    
    with open(CSV_PATH, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            name = row["name"].strip()
            label = row["pack_size_label"].strip()
            
            # 1. Dosage form: check name and label
            form = None
            for pat, df in DOSAGE_PATTERNS:
                if re.search(pat, name, re.IGNORECASE) or re.search(pat, label, re.IGNORECASE):
                    form = df
                    break
            if form:
                parsed_forms[form] += 1
            else:
                unparsed_forms[name[:30]] += 1
                
            # 2. Pack parsing
            m = PACK_REGEX.match(label)
            if m:
                parsed_packs += 1
            else:
                unparsed_packs += 1

    print(f"Total Rows: {total}")
    print(f"Dosage form detected: {sum(parsed_forms.values())}/{total} ({sum(parsed_forms.values())/total*100:.2f}%)")
    print("Dosage forms:")
    for f, c in parsed_forms.most_common(15):
        print(f"  {f}: {c}")
    print(f"\nUnparsed dosage forms: {sum(unparsed_forms.values())}")
    for n, c in unparsed_forms.most_common(5):
        print(f"  {n}: {c}")
        
    print(f"\nPack size parsed: {parsed_packs}/{total} ({parsed_packs/total*100:.2f}%)")
    print(f"Unparsed pack size: {unparsed_packs}")

if __name__ == "__main__":
    test_parsing()
