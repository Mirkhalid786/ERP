import csv
import sys
from collections import Counter

CSV_PATH = "/app/data/catalogue/indian_medicine_data.csv"

def run_inspection():
    print(f"Reading {CSV_PATH}...")
    total_rows = 0
    non_empty = Counter()
    max_lens = {}
    types = Counter()
    discontinued_counts = Counter()
    price_stats = {"valid": 0, "null_or_empty": 0, "zero": 0, "negative": 0, "invalid": 0}
    sample_pack_labels = Counter()
    sample_dosage_forms = Counter()
    
    # Track keys for uniqueness
    exact_names = Counter()
    name_and_mfr = Counter()
    name_and_comp = Counter()
    
    with open(CSV_PATH, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        print(f"Fields: {fields}")
        for k in fields:
            max_lens[k] = 0

        for row in reader:
            total_rows += 1
            for k, v in row.items():
                if v and v.strip():
                    non_empty[k] += 1
                    max_lens[k] = max(max_lens[k], len(v))
            
            med_type = (row.get("type") or "").strip().lower()
            types[med_type] += 1
            
            disc = (row.get("Is_discontinued") or "").strip().upper()
            discontinued_counts[disc] += 1
            
            # Price
            p_str = (row.get("price") or "").strip()
            if not p_str or p_str.lower() in ("null", "none", "nan"):
                price_stats["null_or_empty"] += 1
            else:
                try:
                    p_val = float(p_str)
                    if p_val < 0:
                        price_stats["negative"] += 1
                    elif p_val == 0:
                        price_stats["zero"] += 1
                    else:
                        price_stats["valid"] += 1
                except ValueError:
                    price_stats["invalid"] += 1
                    
            name = (row.get("name") or "").strip()
            mfr = (row.get("manufacturer_name") or "").strip()
            comp = (row.get("salt_composition") or "").strip()
            label = (row.get("pack_size_label") or "").strip()
            
            exact_names[name.lower()] += 1
            name_and_mfr[(name.lower(), mfr.lower())] += 1
            name_and_comp[(name.lower(), comp.lower())] += 1
            
            if total_rows <= 1000:
                if label:
                    sample_pack_labels[label] += 1

    print("\n=== SUMMARY STATS ===")
    print(f"Total Rows: {total_rows}")
    print("\nNon-empty field counts and max string lengths:")
    for k in fields:
        print(f"  - {k}: {non_empty[k]}/{total_rows} ({non_empty[k]/total_rows*100:.1f}%) | Max len: {max_lens[k]}")
        
    print("\nMedicine Type distribution:")
    for t, c in types.most_common(10):
        print(f"  - {t or '<blank>'}: {c}")
        
    print("\nDiscontinued distribution:")
    for d, c in discontinued_counts.most_common():
        print(f"  - {d or '<blank>'}: {c}")
        
    print("\nPrice stats:")
    for k, v in price_stats.items():
        print(f"  - {k}: {v}")
        
    print("\nUniqueness:")
    print(f"  - Unique names: {len(exact_names)}")
    print(f"  - Unique (name, manufacturer): {len(name_and_mfr)}")
    print(f"  - Unique (name, salt_composition): {len(name_and_comp)}")
    
    print("\nTop 15 pack_size_labels in first 1000 rows:")
    for l, c in sample_pack_labels.most_common(15):
        print(f"  - {l}: {c}")

if __name__ == "__main__":
    run_inspection()
