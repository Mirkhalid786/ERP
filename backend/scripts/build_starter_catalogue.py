"""
Builds data/catalogue/starter_generic_catalogue.csv — a STARTER list of common generic formulations used in Indian
retail pharmacy (generic name, standard strength, dosage form). It is not a complete catalogue of medicines in India.

Provenance: compiled for this project from standard generic names and commonly marketed strengths (Indian
Pharmacopoeia naming; most molecules appear in the National List of Essential Medicines 2022). It contains no data
copied from any proprietary or permission-restricted catalogue, and no brand names, prices or pack sizes.

Deliberately left for the pharmacy to set: GST rate (medicine rates changed in 2025), drug schedule, pack sizes
(they differ by brand). "requires_prescription" is conservative: true for everything except clearly OTC items.
Review entries before relying on them.

Run from backend/:  python -m scripts.build_starter_catalogue
"""
import csv
import os

# (generic name, [strengths], dosage form, category, prescription required)
TABLETS = [
    ("Paracetamol", ["500 mg", "650 mg"], "Tablet", "Analgesics & Antipyretics", False),
    ("Ibuprofen", ["200 mg", "400 mg"], "Tablet", "Analgesics & Antipyretics", True),
    ("Ibuprofen + Paracetamol", ["400 mg + 325 mg"], "Tablet", "Analgesics & Antipyretics", True),
    ("Diclofenac Sodium", ["50 mg"], "Tablet", "Analgesics & Antipyretics", True),
    ("Aceclofenac", ["100 mg"], "Tablet", "Analgesics & Antipyretics", True),
    ("Aceclofenac + Paracetamol", ["100 mg + 325 mg"], "Tablet", "Analgesics & Antipyretics", True),
    ("Mefenamic Acid", ["250 mg", "500 mg"], "Tablet", "Analgesics & Antipyretics", True),
    ("Naproxen", ["250 mg", "500 mg"], "Tablet", "Analgesics & Antipyretics", True),
    ("Tramadol Hydrochloride", ["50 mg"], "Tablet", "Analgesics & Antipyretics", True),
    ("Aspirin", ["75 mg", "150 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Amoxicillin + Clavulanic Acid", ["625 mg"], "Tablet", "Antibiotics", True),
    ("Azithromycin", ["250 mg", "500 mg"], "Tablet", "Antibiotics", True),
    ("Ciprofloxacin", ["250 mg", "500 mg"], "Tablet", "Antibiotics", True),
    ("Ofloxacin", ["200 mg", "400 mg"], "Tablet", "Antibiotics", True),
    ("Levofloxacin", ["250 mg", "500 mg"], "Tablet", "Antibiotics", True),
    ("Cefixime", ["200 mg"], "Tablet", "Antibiotics", True),
    ("Cefuroxime Axetil", ["250 mg", "500 mg"], "Tablet", "Antibiotics", True),
    ("Cefpodoxime Proxetil", ["200 mg"], "Tablet", "Antibiotics", True),
    ("Metronidazole", ["200 mg", "400 mg"], "Tablet", "Antibiotics", True),
    ("Tinidazole", ["500 mg"], "Tablet", "Antibiotics", True),
    ("Nitrofurantoin", ["100 mg"], "Tablet", "Antibiotics", True),
    ("Cotrimoxazole (Sulfamethoxazole + Trimethoprim)", ["400 mg + 80 mg", "800 mg + 160 mg"], "Tablet", "Antibiotics", True),
    ("Doxycycline", ["100 mg"], "Tablet", "Antibiotics", True),
    ("Fluconazole", ["150 mg"], "Tablet", "Antibiotics", True),
    ("Albendazole", ["400 mg"], "Tablet", "Gastrointestinal & Antacids", True),
    ("Ivermectin", ["6 mg", "12 mg"], "Tablet", "Antibiotics", True),
    ("Acyclovir", ["400 mg", "800 mg"], "Tablet", "Antibiotics", True),
    ("Hydroxychloroquine Sulphate", ["200 mg"], "Tablet", "Antibiotics", True),
    ("Cetirizine Hydrochloride", ["10 mg"], "Tablet", "Respiratory & Antiallergic", False),
    ("Levocetirizine Dihydrochloride", ["5 mg"], "Tablet", "Respiratory & Antiallergic", True),
    ("Fexofenadine Hydrochloride", ["120 mg", "180 mg"], "Tablet", "Respiratory & Antiallergic", True),
    ("Loratadine", ["10 mg"], "Tablet", "Respiratory & Antiallergic", True),
    ("Chlorpheniramine Maleate", ["4 mg"], "Tablet", "Respiratory & Antiallergic", True),
    ("Montelukast", ["10 mg"], "Tablet", "Respiratory & Antiallergic", True),
    ("Montelukast + Levocetirizine", ["10 mg + 5 mg"], "Tablet", "Respiratory & Antiallergic", True),
    ("Salbutamol", ["2 mg", "4 mg"], "Tablet", "Respiratory & Antiallergic", True),
    ("Prednisolone", ["5 mg", "10 mg"], "Tablet", "Respiratory & Antiallergic", True),
    ("Dexamethasone", ["0.5 mg"], "Tablet", "Respiratory & Antiallergic", True),
    ("Methylprednisolone", ["4 mg", "8 mg"], "Tablet", "Respiratory & Antiallergic", True),
    ("Pantoprazole", ["40 mg"], "Tablet", "Gastrointestinal & Antacids", True),
    ("Rabeprazole Sodium", ["20 mg"], "Tablet", "Gastrointestinal & Antacids", True),
    ("Famotidine", ["20 mg", "40 mg"], "Tablet", "Gastrointestinal & Antacids", True),
    ("Domperidone", ["10 mg"], "Tablet", "Gastrointestinal & Antacids", True),
    ("Ondansetron", ["4 mg", "8 mg"], "Tablet", "Gastrointestinal & Antacids", True),
    ("Metoclopramide", ["10 mg"], "Tablet", "Gastrointestinal & Antacids", True),
    ("Loperamide Hydrochloride", ["2 mg"], "Tablet", "Gastrointestinal & Antacids", True),
    ("Dicyclomine Hydrochloride", ["10 mg", "20 mg"], "Tablet", "Gastrointestinal & Antacids", True),
    ("Drotaverine Hydrochloride", ["40 mg", "80 mg"], "Tablet", "Gastrointestinal & Antacids", True),
    ("Bisacodyl", ["5 mg"], "Tablet", "Gastrointestinal & Antacids", False),
    ("Metformin Hydrochloride", ["500 mg", "850 mg", "1000 mg"], "Tablet", "Antidiabetic", True),
    ("Metformin Hydrochloride SR", ["500 mg", "1000 mg"], "Tablet", "Antidiabetic", True),
    ("Glimepiride", ["1 mg", "2 mg"], "Tablet", "Antidiabetic", True),
    ("Gliclazide", ["40 mg", "80 mg"], "Tablet", "Antidiabetic", True),
    ("Glimepiride + Metformin", ["1 mg + 500 mg", "2 mg + 500 mg"], "Tablet", "Antidiabetic", True),
    ("Sitagliptin", ["50 mg", "100 mg"], "Tablet", "Antidiabetic", True),
    ("Vildagliptin", ["50 mg"], "Tablet", "Antidiabetic", True),
    ("Teneligliptin", ["20 mg"], "Tablet", "Antidiabetic", True),
    ("Dapagliflozin", ["5 mg", "10 mg"], "Tablet", "Antidiabetic", True),
    ("Empagliflozin", ["10 mg", "25 mg"], "Tablet", "Antidiabetic", True),
    ("Pioglitazone", ["15 mg", "30 mg"], "Tablet", "Antidiabetic", True),
    ("Voglibose", ["0.2 mg", "0.3 mg"], "Tablet", "Antidiabetic", True),
    ("Amlodipine", ["2.5 mg", "5 mg", "10 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Telmisartan", ["20 mg", "40 mg", "80 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Telmisartan + Amlodipine", ["40 mg + 5 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Telmisartan + Hydrochlorothiazide", ["40 mg + 12.5 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Losartan Potassium", ["25 mg", "50 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Olmesartan Medoxomil", ["20 mg", "40 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Ramipril", ["2.5 mg", "5 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Enalapril Maleate", ["5 mg", "10 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Atenolol", ["25 mg", "50 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Metoprolol Succinate ER", ["25 mg", "50 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Bisoprolol Fumarate", ["2.5 mg", "5 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Carvedilol", ["3.125 mg", "6.25 mg", "12.5 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Cilnidipine", ["10 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Hydrochlorothiazide", ["12.5 mg", "25 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Furosemide", ["40 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Torsemide", ["10 mg", "20 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Spironolactone", ["25 mg", "50 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Atorvastatin", ["10 mg", "20 mg", "40 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Rosuvastatin", ["5 mg", "10 mg", "20 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Clopidogrel", ["75 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Clopidogrel + Aspirin", ["75 mg + 75 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Isosorbide Mononitrate", ["20 mg", "30 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Digoxin", ["0.25 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Warfarin Sodium", ["1 mg", "2 mg", "5 mg"], "Tablet", "Cardiovascular & Antihypertensive", True),
    ("Levothyroxine Sodium", ["25 mcg", "50 mcg", "75 mcg", "100 mcg"], "Tablet", "Endocrine & Thyroid", True),
    ("Carbimazole", ["5 mg", "10 mg"], "Tablet", "Endocrine & Thyroid", True),
    ("Folic Acid", ["5 mg"], "Tablet", "Vitamins & Supplements", False),
    ("Ferrous Sulphate + Folic Acid", ["Standard"], "Tablet", "Vitamins & Supplements", False),
    ("Calcium Carbonate + Vitamin D3", ["500 mg + 250 IU"], "Tablet", "Vitamins & Supplements", False),
    ("Vitamin B Complex", ["Standard"], "Tablet", "Vitamins & Supplements", False),
    ("Methylcobalamin", ["500 mcg", "1500 mcg"], "Tablet", "Vitamins & Supplements", False),
    ("Ascorbic Acid (Vitamin C)", ["500 mg"], "Tablet", "Vitamins & Supplements", False),
    ("Zinc Sulphate (Dispersible)", ["20 mg"], "Tablet", "Vitamins & Supplements", False),
    ("Alprazolam", ["0.25 mg", "0.5 mg"], "Tablet", "Neurology & Psychiatry", True),
    ("Clonazepam", ["0.5 mg"], "Tablet", "Neurology & Psychiatry", True),
    ("Escitalopram", ["10 mg"], "Tablet", "Neurology & Psychiatry", True),
    ("Sertraline", ["50 mg"], "Tablet", "Neurology & Psychiatry", True),
    ("Amitriptyline", ["10 mg", "25 mg"], "Tablet", "Neurology & Psychiatry", True),
    ("Gabapentin", ["100 mg", "300 mg"], "Tablet", "Neurology & Psychiatry", True),
    ("Pregabalin", ["75 mg"], "Capsule", "Neurology & Psychiatry", True),
    ("Levetiracetam", ["250 mg", "500 mg"], "Tablet", "Neurology & Psychiatry", True),
    ("Sodium Valproate", ["200 mg", "500 mg"], "Tablet", "Neurology & Psychiatry", True),
    ("Phenytoin Sodium", ["100 mg"], "Tablet", "Neurology & Psychiatry", True),
    ("Carbamazepine", ["200 mg"], "Tablet", "Neurology & Psychiatry", True),
    ("Betahistine", ["8 mg", "16 mg"], "Tablet", "Neurology & Psychiatry", True),
    ("Cinnarizine", ["25 mg"], "Tablet", "Neurology & Psychiatry", True),
    ("Tamsulosin", ["0.4 mg"], "Capsule", "Urology", True),
    ("Finasteride", ["5 mg"], "Tablet", "Urology", True),
    ("Sildenafil", ["50 mg"], "Tablet", "Urology", True),
    ("Tranexamic Acid", ["500 mg"], "Tablet", "Women's Health", True),
    ("Norethisterone", ["5 mg"], "Tablet", "Women's Health", True),
    ("Allopurinol", ["100 mg", "300 mg"], "Tablet", "Musculoskeletal & Gout", True),
    ("Febuxostat", ["40 mg", "80 mg"], "Tablet", "Musculoskeletal & Gout", True),
    ("Colchicine", ["0.5 mg"], "Tablet", "Musculoskeletal & Gout", True),
]
CAPSULES = [
    ("Amoxicillin", ["250 mg", "500 mg"], "Capsule", "Antibiotics", True),
    ("Cephalexin", ["250 mg", "500 mg"], "Capsule", "Antibiotics", True),
    ("Doxycycline", ["100 mg"], "Capsule", "Antibiotics", True),
    ("Omeprazole", ["20 mg"], "Capsule", "Gastrointestinal & Antacids", True),
    ("Esomeprazole", ["40 mg"], "Capsule", "Gastrointestinal & Antacids", True),
    ("Pantoprazole + Domperidone SR", ["40 mg + 30 mg"], "Capsule", "Gastrointestinal & Antacids", True),
    ("Rabeprazole + Domperidone SR", ["20 mg + 30 mg"], "Capsule", "Gastrointestinal & Antacids", True),
    ("Itraconazole", ["100 mg", "200 mg"], "Capsule", "Antibiotics", True),
    ("Vitamin D3 (Cholecalciferol)", ["60000 IU"], "Capsule", "Vitamins & Supplements", False),
    ("Vitamin E", ["400 mg"], "Capsule", "Vitamins & Supplements", False),
    ("Omega-3 Fatty Acids", ["1000 mg"], "Capsule", "Vitamins & Supplements", False),
    ("Lactic Acid Bacillus (Probiotic)", ["Standard"], "Capsule", "Gastrointestinal & Antacids", False),
]
LIQUIDS = [
    ("Paracetamol", ["125 mg/5 ml", "250 mg/5 ml"], "Syrup", "Analgesics & Antipyretics", False),
    ("Paracetamol", ["100 mg/ml"], "Oral Drops", "Analgesics & Antipyretics", False),
    ("Ibuprofen", ["100 mg/5 ml"], "Suspension", "Analgesics & Antipyretics", True),
    ("Amoxicillin", ["125 mg/5 ml", "250 mg/5 ml"], "Dry Syrup", "Antibiotics", True),
    ("Amoxicillin + Clavulanic Acid", ["228.5 mg/5 ml", "457 mg/5 ml"], "Dry Syrup", "Antibiotics", True),
    ("Azithromycin", ["100 mg/5 ml", "200 mg/5 ml"], "Suspension", "Antibiotics", True),
    ("Cefixime", ["50 mg/5 ml", "100 mg/5 ml"], "Dry Syrup", "Antibiotics", True),
    ("Cefpodoxime Proxetil", ["50 mg/5 ml"], "Dry Syrup", "Antibiotics", True),
    ("Metronidazole", ["200 mg/5 ml"], "Suspension", "Antibiotics", True),
    ("Albendazole", ["200 mg/5 ml"], "Suspension", "Gastrointestinal & Antacids", True),
    ("Cetirizine Hydrochloride", ["5 mg/5 ml"], "Syrup", "Respiratory & Antiallergic", False),
    ("Levocetirizine Dihydrochloride", ["2.5 mg/5 ml"], "Syrup", "Respiratory & Antiallergic", True),
    ("Montelukast + Levocetirizine", ["4 mg + 2.5 mg/5 ml"], "Syrup", "Respiratory & Antiallergic", True),
    ("Salbutamol", ["2 mg/5 ml"], "Syrup", "Respiratory & Antiallergic", True),
    ("Ambroxol Hydrochloride", ["15 mg/5 ml", "30 mg/5 ml"], "Syrup", "Respiratory & Antiallergic", True),
    ("Dextromethorphan Hydrobromide + Chlorpheniramine", ["10 mg + 2 mg/5 ml"], "Syrup", "Respiratory & Antiallergic", True),
    ("Ondansetron", ["2 mg/5 ml"], "Syrup", "Gastrointestinal & Antacids", True),
    ("Domperidone", ["1 mg/ml"], "Suspension", "Gastrointestinal & Antacids", True),
    ("Lactulose", ["10 g/15 ml"], "Solution", "Gastrointestinal & Antacids", False),
    ("Aluminium Hydroxide + Magnesium Hydroxide + Simethicone", ["Standard"], "Suspension", "Gastrointestinal & Antacids", False),
    ("Sucralfate", ["1 g/10 ml"], "Suspension", "Gastrointestinal & Antacids", True),
    ("Ferric Ammonium Citrate + Folic Acid", ["Standard"], "Syrup", "Vitamins & Supplements", False),
    ("Multivitamin", ["Standard"], "Syrup", "Vitamins & Supplements", False),
    ("Calcium + Vitamin D3", ["Standard"], "Suspension", "Vitamins & Supplements", False),
    ("Vitamin D3 (Cholecalciferol)", ["400 IU/ml"], "Oral Drops", "Vitamins & Supplements", False),
    ("Zinc Sulphate", ["20 mg/5 ml"], "Syrup", "Vitamins & Supplements", False),
    ("Sodium Valproate", ["200 mg/5 ml"], "Syrup", "Neurology & Psychiatry", True),
]
INJECTIONS = [
    ("Ceftriaxone", ["250 mg", "500 mg", "1 g"], "Injection (Vial)", "Antibiotics", True),
    ("Amikacin", ["100 mg/2 ml", "500 mg/2 ml"], "Injection (Vial)", "Antibiotics", True),
    ("Gentamicin", ["80 mg/2 ml"], "Injection (Vial)", "Antibiotics", True),
    ("Pantoprazole", ["40 mg"], "Injection (Vial)", "Gastrointestinal & Antacids", True),
    ("Ondansetron", ["2 mg/ml"], "Injection (Ampoule)", "Gastrointestinal & Antacids", True),
    ("Diclofenac Sodium", ["75 mg/ml"], "Injection (Ampoule)", "Analgesics & Antipyretics", True),
    ("Paracetamol", ["150 mg/ml"], "Injection (Ampoule)", "Analgesics & Antipyretics", True),
    ("Dexamethasone Sodium Phosphate", ["4 mg/ml"], "Injection (Vial)", "Respiratory & Antiallergic", True),
    ("Pheniramine Maleate", ["22.75 mg/ml"], "Injection (Ampoule)", "Respiratory & Antiallergic", True),
    ("Insulin Human Regular", ["40 IU/ml", "100 IU/ml"], "Injection (Vial)", "Antidiabetic", True),
    ("Insulin Human Premix 30/70", ["40 IU/ml", "100 IU/ml"], "Injection (Vial)", "Antidiabetic", True),
    ("Methylcobalamin", ["500 mcg/ml"], "Injection (Ampoule)", "Vitamins & Supplements", True),
    ("Vitamin B Complex", ["Standard"], "Injection (Vial)", "Vitamins & Supplements", True),
    ("Tetanus Toxoid (Adsorbed)", ["0.5 ml"], "Injection (Ampoule)", "Injectables & IV Fluids", True),
    ("Sodium Chloride 0.9%", ["500 ml"], "Infusion", "Injectables & IV Fluids", True),
    ("Dextrose 5%", ["500 ml"], "Infusion", "Injectables & IV Fluids", True),
    ("Ringer Lactate", ["500 ml"], "Infusion", "Injectables & IV Fluids", True),
    ("Water for Injection", ["10 ml"], "Injection (Ampoule)", "Injectables & IV Fluids", True),
]
DROPS_TOPICALS = [
    ("Ciprofloxacin", ["0.3% w/v"], "Eye/Ear Drops", "Antibiotics", True),
    ("Moxifloxacin", ["0.5% w/v"], "Eye Drops", "Antibiotics", True),
    ("Tobramycin", ["0.3% w/v"], "Eye Drops", "Antibiotics", True),
    ("Carboxymethylcellulose Sodium", ["0.5% w/v"], "Eye Drops", "Ophthalmic", False),
    ("Olopatadine", ["0.1% w/v"], "Eye Drops", "Respiratory & Antiallergic", True),
    ("Timolol Maleate", ["0.5% w/v"], "Eye Drops", "Ophthalmic", True),
    ("Xylometazoline Hydrochloride", ["0.05% w/v", "0.1% w/v"], "Nasal Drops", "Respiratory & Antiallergic", False),
    ("Sodium Chloride (Saline)", ["0.65% w/v"], "Nasal Drops", "Respiratory & Antiallergic", False),
    ("Fluticasone Propionate", ["50 mcg/dose"], "Nasal Spray", "Respiratory & Antiallergic", True),
    ("Clotrimazole + Lignocaine", ["1% + 2% w/v"], "Ear Drops", "Antibiotics", True),
    ("Paradichlorobenzene + Benzocaine (Wax Softener)", ["Standard"], "Ear Drops", "Over-The-Counter (OTC)", False),
    ("Clotrimazole", ["1% w/w"], "Cream", "Dermatological & Topicals", False),
    ("Ketoconazole", ["2% w/w"], "Cream", "Dermatological & Topicals", True),
    ("Luliconazole", ["1% w/w"], "Cream", "Dermatological & Topicals", True),
    ("Terbinafine", ["1% w/w"], "Cream", "Dermatological & Topicals", True),
    ("Mupirocin", ["2% w/w"], "Ointment", "Dermatological & Topicals", True),
    ("Fusidic Acid", ["2% w/w"], "Cream", "Dermatological & Topicals", True),
    ("Silver Sulfadiazine", ["1% w/w"], "Cream", "Dermatological & Topicals", True),
    ("Povidone-Iodine", ["5% w/w"], "Ointment", "Dermatological & Topicals", False),
    ("Betamethasone Valerate", ["0.1% w/w"], "Cream", "Dermatological & Topicals", True),
    ("Clobetasol Propionate", ["0.05% w/w"], "Cream", "Dermatological & Topicals", True),
    ("Hydrocortisone", ["1% w/w"], "Cream", "Dermatological & Topicals", True),
    ("Permethrin", ["5% w/w"], "Cream", "Dermatological & Topicals", True),
    ("Diclofenac Diethylamine", ["1.16% w/w"], "Gel", "Analgesics & Antipyretics", False),
    ("Lignocaine Hydrochloride", ["2% w/w"], "Gel", "Dermatological & Topicals", True),
    ("Calamine Lotion", ["Standard"], "Lotion", "Dermatological & Topicals", False),
    ("Adapalene", ["0.1% w/w"], "Gel", "Dermatological & Topicals", True),
    ("Benzoyl Peroxide", ["2.5% w/w"], "Gel", "Dermatological & Topicals", True),
    ("Clotrimazole", ["1% w/w"], "Dusting Powder", "Dermatological & Topicals", False),
]
RESPIRATORY = [
    ("Salbutamol", ["100 mcg/dose"], "Inhaler", "Respiratory & Antiallergic", True),
    ("Budesonide", ["200 mcg/dose"], "Inhaler", "Respiratory & Antiallergic", True),
    ("Formoterol + Budesonide", ["6 mcg + 200 mcg/dose"], "Inhaler", "Respiratory & Antiallergic", True),
    ("Salmeterol + Fluticasone", ["25 mcg + 250 mcg/dose"], "Inhaler", "Respiratory & Antiallergic", True),
    ("Tiotropium", ["9 mcg/dose"], "Inhaler", "Respiratory & Antiallergic", True),
    ("Levosalbutamol", ["0.63 mg/2.5 ml"], "Respules", "Respiratory & Antiallergic", True),
    ("Budesonide", ["0.5 mg/2 ml"], "Respules", "Respiratory & Antiallergic", True),
    ("Ipratropium + Levosalbutamol", ["500 mcg + 1.25 mg/2.5 ml"], "Respules", "Respiratory & Antiallergic", True),
]
OTC_CONSUMABLES = [
    ("Oral Rehydration Salts (WHO formula)", ["21.8 g"], "Sachet", "Over-The-Counter (OTC)", False, "3004"),
    ("Activated Charcoal + Simethicone", ["Standard"], "Tablet", "Gastrointestinal & Antacids", False, "3004"),
    ("Isabgol (Psyllium Husk)", ["Standard"], "Powder", "Gastrointestinal & Antacids", False, "3004"),
    ("Absorbent Cotton Wool IP", ["100 g"], "Roll", "Surgicals & Consumables", False, "3005"),
    ("Roller Bandage", ["10 cm x 4 m"], "Piece", "Surgicals & Consumables", False, "3005"),
    ("Crepe Bandage", ["10 cm"], "Piece", "Surgicals & Consumables", False, "3005"),
    ("Adhesive Plaster (Surgical Tape)", ["2.5 cm"], "Roll", "Surgicals & Consumables", False, "3005"),
    ("Sterile Gauze Swabs", ["10 cm x 10 cm"], "Pack", "Surgicals & Consumables", False, "3005"),
    ("Adhesive Wound Dressing Strips", ["Standard"], "Pack", "Surgicals & Consumables", False, "3005"),
    ("Disposable Syringe with Needle", ["2 ml", "5 ml", "10 ml"], "Piece", "Surgicals & Consumables", False, "9018"),
    ("Insulin Syringe", ["1 ml (40 IU)", "1 ml (100 IU)"], "Piece", "Surgicals & Consumables", False, "9018"),
    ("IV Cannula", ["20 G", "22 G", "24 G"], "Piece", "Surgicals & Consumables", False, "9018"),
    ("Infusion Set", ["Standard"], "Piece", "Surgicals & Consumables", False, "9018"),
    ("Examination Gloves (Latex)", ["Medium", "Large"], "Pair", "Surgicals & Consumables", False, "4015"),
    ("Surgical Face Mask (3-ply)", ["Standard"], "Piece", "Surgicals & Consumables", False, "6307"),
    ("Clinical Thermometer (Digital)", ["Standard"], "Piece", "Surgicals & Consumables", False, "9025"),
    ("Hand Sanitizer (Alcohol-based)", ["100 ml", "500 ml"], "Bottle", "Over-The-Counter (OTC)", False, "3808"),
    ("Antiseptic Liquid (Chloroxylenol)", ["100 ml"], "Bottle", "Over-The-Counter (OTC)", False, "3808"),
]

# Dosage form -> stock (base) unit. Packs (strip/box) are added per brand after import.
BASE_UNIT = {
    "Tablet": "Tablet", "Capsule": "Capsule", "Syrup": "Bottle", "Suspension": "Bottle", "Dry Syrup": "Bottle",
    "Oral Drops": "Bottle", "Solution": "Bottle", "Injection (Vial)": "Vial", "Injection (Ampoule)": "Ampoule",
    "Infusion": "Bottle", "Eye Drops": "Bottle", "Eye/Ear Drops": "Bottle", "Ear Drops": "Bottle", "Nasal Drops": "Bottle",
    "Nasal Spray": "Bottle", "Cream": "Tube", "Ointment": "Tube", "Gel": "Tube", "Lotion": "Bottle",
    "Dusting Powder": "Bottle", "Inhaler": "Piece", "Respules": "Ampoule", "Sachet": "Piece", "Powder": "Piece",
    "Roll": "Piece", "Piece": "Piece", "Pack": "Piece", "Pair": "Piece", "Bottle": "Bottle",
}

COLUMNS = ["name", "generic_name", "strength", "dosage_form", "category", "hsn_code", "requires_prescription", "base_unit"]


def rows():
    groups = [TABLETS, CAPSULES, LIQUIDS, INJECTIONS, DROPS_TOPICALS, RESPIRATORY]
    for group in groups:
        for generic, strengths, form, category, rx in group:
            for strength in strengths:
                suffix = "" if strength == "Standard" else f" {strength}"
                yield [f"{generic}{suffix} {form}", generic, "" if strength == "Standard" else strength, form, category,
                       "3004", "true" if rx else "false", BASE_UNIT[form]]
    for generic, strengths, form, category, rx, hsn in OTC_CONSUMABLES:
        for strength in strengths:
            suffix = "" if strength == "Standard" else f" {strength}"
            yield [f"{generic}{suffix}", generic, "" if strength == "Standard" else strength, form, category,
                   hsn, "true" if rx else "false", BASE_UNIT[form]]


def main():
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "catalogue",
                       "starter_generic_catalogue.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    data = list(rows())
    names = [r[0].lower() for r in data]
    assert len(names) == len(set(names)), "duplicate names in the starter catalogue"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(data)
    print(f"{len(data)} rows -> {out}")


if __name__ == "__main__":
    main()
