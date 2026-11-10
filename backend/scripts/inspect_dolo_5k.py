import csv

CSV_PATH = "/app/data/catalogue/indian_medicine_data.csv"

def find_dolo_first_5000():
    with open(CSV_PATH, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader, 1):
            if idx > 5000:
                break
            name = (row.get("name") or "").strip()
            if "dolo" in name.lower():
                print(f"Row {idx}: ID={row.get('id')}, Name={row.get('name')}, Mfr={row.get('manufacturer_name')}")

if __name__ == "__main__":
    find_dolo_first_5000()
