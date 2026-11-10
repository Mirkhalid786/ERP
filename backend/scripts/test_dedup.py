import csv
import re
from collections import Counter

CSV_PATH = "/app/data/catalogue/indian_medicine_data.csv"

def norm_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())

def test_dedup():
    by_name = set()
    by_name_mfr = set()
    by_name_mfr_comp = set()
    by_name_mfr_pack = set()
    
    with open(CSV_PATH, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            n = norm_key(row["name"])
            m = norm_key(row["manufacturer_name"])
            c = norm_key(row.get("short_composition1", "") + row.get("short_composition2", ""))
            p = norm_key(row.get("pack_size_label", ""))
            
            by_name.add(n)
            by_name_mfr.add((n, m))
            by_name_mfr_comp.add((n, m, c))
            by_name_mfr_pack.add((n, m, p))

    print(f"Total rows: 253973")
    print(f"Unique by norm_name: {len(by_name)}")
    print(f"Unique by (norm_name, norm_mfr): {len(by_name_mfr)}")
    print(f"Unique by (norm_name, norm_mfr, norm_comp): {len(by_name_mfr_comp)}")
    print(f"Unique by (norm_name, norm_mfr, norm_pack): {len(by_name_mfr_pack)}")

if __name__ == "__main__":
    test_dedup()
