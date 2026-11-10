import csv
import re
from collections import Counter

CSV_PATH = "/app/data/catalogue/indian_medicine_data.csv"

def inspect_details():
    comp_samples = []
    dosage_forms = Counter()
    pack_units = Counter()
    pack_sizes = Counter()
    missing_comp = 0
    
    # Check how many distinct manufacturers exist
    manufacturers = set()
    
    with open(CSV_PATH, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            mfr = row["manufacturer_name"].strip()
            manufacturers.add(mfr.lower())
            
            c1 = (row.get("short_composition1") or "").strip()
            c2 = (row.get("short_composition2") or "").strip()
            cs = (row.get("salt_composition") or "").strip()
            
            full_comp = cs if cs else (f"{c1} + {c2}" if c2 else c1)
            if not full_comp:
                missing_comp += 1
                
            label = (row.get("pack_size_label") or "").strip()
            # Try parsing label: e.g. "strip of 10 tablets", "bottle of 100 ml Syrup", "box of 1 vial", "tube of 15 gm Gel"
            m = re.match(r"(?i)^(strip|box|bottle|tube|vial|ampoule|packet|blister|pack|jar|sachet|can|carton|tin)\s+of\s+([0-9.]+)\s*(.*)$", label)
            if m:
                p_unit, p_size, p_form = m.groups()
                pack_units[p_unit.title()] += 1
                # Check form
                dosage_forms[p_form.strip().title()] += 1
            else:
                pack_units["<unmatched: " + label[:20] + ">"] += 1
                
            if i < 10:
                comp_samples.append({
                    "name": row["name"],
                    "c1": c1,
                    "c2": c2,
                    "salt": cs,
                    "combined": full_comp,
                    "label": label
                })

    print(f"Total Unique Manufacturers: {len(manufacturers)}")
    print(f"Missing composition: {missing_comp}")
    print("\nTop 15 Pack Units parsed from label:")
    for u, c in pack_units.most_common(15):
        print(f"  {u}: {c}")
        
    print("\nTop 20 Dosage Forms / descriptions parsed from label:")
    for df, c in dosage_forms.most_common(20):
        print(f"  {df}: {c}")
        
    print("\nFirst 10 Composition Samples:")
    for s in comp_samples:
        print(f"  Name: {s['name']}")
        print(f"    Combined Salt: {s['combined']}")
        print(f"    Label: {s['label']}")

if __name__ == "__main__":
    inspect_details()
