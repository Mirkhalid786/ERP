import csv

CSV_PATH = "/app/data/catalogue/indian_medicine_data.csv"

def find_dolo():
    with open(CSV_PATH, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        matches = []
        for idx, row in enumerate(reader, 1):
            name = (row.get("name") or "").strip()
            if "dolo 650" in name.lower() or name.lower() == "dolo 650 tablet":
                matches.append((idx, row))
                if len(matches) >= 5:
                    break
        for idx, r in matches:
            print(f"Row {idx}: ID={r.get('id')}, Name={r.get('name')}, Mfr={r.get('manufacturer_name')}, Comp={r.get('salt_composition')}, Price={r.get('price')}, Label={r.get('pack_size_label')}")

if __name__ == "__main__":
    find_dolo()
