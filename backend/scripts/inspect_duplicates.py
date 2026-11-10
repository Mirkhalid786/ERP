import csv
from collections import defaultdict

CSV_PATH = "/app/data/catalogue/indian_medicine_data.csv"

def inspect_duplicates():
    by_name = defaultdict(list)
    with open(CSV_PATH, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            name_clean = row["name"].strip().lower()
            by_name[name_clean].append({
                "id": row["id"],
                "name": row["name"],
                "mfr": row["manufacturer_name"],
                "price": row["price"],
                "pack": row["pack_size_label"],
                "c1": row.get("short_composition1", ""),
                "c2": row.get("short_composition2", ""),
                "disc": row.get("Is_discontinued", "")
            })
            
    dup_names = {k: v for k, v in by_name.items() if len(v) > 1}
    print(f"Total Unique Names: {len(by_name)}")
    print(f"Names appearing more than once: {len(dup_names)}")
    
    # Check what differs among duplicates
    sample_dups = list(dup_names.items())[:10]
    for name, rows in sample_dups:
        print(f"\n--- Name: '{rows[0]['name']}' (appears {len(rows)} times) ---")
        for r in rows:
            print(f"  ID: {r['id']} | MFR: {r['mfr']} | Price: {r['price']} | Pack: {r['pack']} | Salt: {r['c1']} {r['c2']} | Disc: {r['disc']}")

if __name__ == "__main__":
    inspect_duplicates()
