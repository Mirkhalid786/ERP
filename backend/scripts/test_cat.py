import csv
import re
from collections import Counter

CSV_PATH = "/app/data/catalogue/indian_medicine_data.csv"

CATEGORY_RULES = [
    ("Antibiotics", re.compile(r"(?i)\b(amoxycillin|clavulanic|azithromycin|cefixime|ciprofloxacin|ofloxacin|cefpodoxime|metronidazole|doxycycline|levofloxacin|ampicillin|meropenem|ceftriaxone|amikacin|gentamicin|clindamycin|clarithromycin|linezolid|moxifloxacin|norfloxacin|piperacillin|tazobactam|nitrofurantoin|cefuroxime|erythromycin)\b")),
    ("Analgesics & Antipyretics", re.compile(r"(?i)\b(paracetamol|ibuprofen|diclofenac|aceclofenac|tramadol|mefenamic|etoricoxib|ketorolac|aspirin|nimesulide|piroxicam|naproxen|indomethacin|lornoxicam)\b")),
    ("Gastrointestinal & Antacids", re.compile(r"(?i)\b(pantoprazole|omeprazole|rabeprazole|esomeprazole|ranitidine|famotidine|domperidone|ondansetron|sucralfate|magaldrate|simethicone|lactulose|sodium picosulfate|bisacodyl|mesalamine|metoclopramide|itopride)\b")),
    ("Cardiovascular & Antihypertensive", re.compile(r"(?i)\b(amlodipine|telmisartan|losartan|atorvastatin|rosuvastatin|metoprolol|atenolol|ramipril|enalapril|clopidogrel|ticagrelor|olmesartan|cilnidipine|diltiazem|verapamil|nebivolol|bisoprolol|carvedilol|fenofibrate)\b")),
    ("Respiratory & Antiallergic", re.compile(r"(?i)\b(cetirizine|levocetirizine|fexofenadine|montelukast|salbutamol|ambroxol|guaifenesin|dextromethorphan|phenylephrine|chlorpheniramine|budesonide|formoterol|levosalbutamol|terbutaline|theophylline|bilastine|hydroxyzine)\b")),
    ("Antidiabetic", re.compile(r"(?i)\b(metformin|glimepiride|gliclazide|vildagliptin|sitagliptin|teneligliptin|dapagliflozin|empagliflozin|insulin|pioglitazone|glipizide|voglibose)\b")),
    ("Dermatological & Topicals", re.compile(r"(?i)\b(clobetasol|betamethasone|mupirocin|fusidic|ketoconazole|clotrimazole|permethrin|adapalene|miconazole|terbinafine|fluconazole|itraconazole|calamine)\b")),
    ("Vitamins & Supplements", re.compile(r"(?i)\b(calcium|vitamin|folic acid|zinc|iron|ferrous|methylcobalamin|ascorbic|cyanocobalamin|multivitamin|cholecalciferol|protein|biotin)\b")),
    ("Central Nervous System", re.compile(r"(?i)\b(alprazolam|clonazepam|escitalopram|sertraline|fluoxetine|gabapentin|pregabalin|duloxetine|olanzapine|quetiapine|risperidone|levetiracetam|sodium valproate|carbamazepine)\b")),
]

def test_cat():
    cats = Counter()
    total = 0
    with open(CSV_PATH, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            comp = (row.get("short_composition1", "") + " " + row.get("short_composition2", "") + " " + row.get("salt_composition", "")).strip()
            name = row["name"].strip()
            
            matched = None
            for cat_name, rgx in CATEGORY_RULES:
                if rgx.search(comp) or rgx.search(name):
                    matched = cat_name
                    break
            if not matched:
                matched = "General Pharmaceuticals"
            cats[matched] += 1
            
    print(f"Total rows: {total}")
    for c, cnt in cats.most_common():
        print(f"  {c}: {cnt} ({cnt/total*100:.1f}%)")

if __name__ == "__main__":
    test_cat()
