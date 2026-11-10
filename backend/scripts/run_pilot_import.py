"""
Safe Runner for Step 2 Pilot Import (5,000 records).
Validates pre-conditions, previews 5,000 records, executes batched transactional import,
and verifies post-conditions (counts, samples, and searches).
"""
import asyncio
import json
import os
import sys
from decimal import Decimal

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.auth import Pharmacy, User, Role
from app.models.inventory import (
    Medicine, Manufacturer, Category, Unit, MedicineUnitConversion,
    MedicineBatch, StockMovement,
)
from app.services.indian_medicine_import import (
    DATASET_CSV_PATH,
    preview_indian_catalogue,
    import_indian_catalogue,
)


async def run_pilot():
    print("=" * 70)
    print("STEP 2: 5,000-RECORD PILOT IMPORT EXECUTION & VERIFICATION")
    print("=" * 70)

    async with AsyncSessionLocal() as session:
        # 1. Pre-condition Check: Migration revision & health
        rev_res = await session.execute(text("SELECT version_num FROM alembic_version"))
        rev = rev_res.scalar_one_or_none()
        print(f"\n[1] Database Revision Check: {rev}")
        if rev != "013":
            print(f"ABORT: Expected revision '013', but found '{rev}'")
            sys.exit(1)

        # Check Pharmacy & Admin User
        pharm_res = await session.execute(select(Pharmacy).where(Pharmacy.id == 1))
        pharmacy = pharm_res.scalar_one_or_none()
        if not pharmacy:
            print("ABORT: Pharmacy ID 1 not found!")
            sys.exit(1)
        print(f"    Pharmacy verified: {pharmacy.name} (ID: {pharmacy.id})")

        admin_res = await session.execute(select(User).where(User.username == "admin"))
        admin = admin_res.scalar_one_or_none()
        if not admin:
            print("ABORT: Admin user not found!")
            sys.exit(1)
        print(f"    Admin verified: {admin.name} (@{admin.username}, ID: {admin.id})")

        # Initial Counts
        med_cnt_pre = (await session.execute(select(func.count(Medicine.id)).where(Medicine.pharmacy_id == 1))).scalar()
        print(f"    Initial medicines in DB: {med_cnt_pre}")

    # 2. Preview 5,000 valid records
    print("\n[2] Previewing first 5,000 valid records from dataset...")
    preview_res = preview_indian_catalogue(csv_path=DATASET_CSV_PATH, max_rows=5000)
    print(f"    Total source records read: {preview_res['total_source_records']}")
    print(f"    Valid records: {preview_res['valid_records']}")
    print(f"    Unique medicines: {preview_res['unique_medicines']}")
    print(f"    Duplicate variants skipped: {preview_res['duplicate_variants']}")
    print(f"    Rejected records: {preview_res['rejected_records']}")
    print(f"    Unique manufacturers: {preview_res['unique_manufacturers']}")
    print(f"    Packaging unit conversions: {preview_res['conversions_count']}")
    print(f"    Discontinued items: {preview_res['discontinued_count']}")
    print(f"    Active marketed items: {preview_res['active_marketed_count']}")
    print("    Field coverage:")
    for k, v in preview_res['field_coverage'].items():
        print(f"      - {k}: {v}")

    # 3. Execute Pilot Import (5,000 limit, batch_size=2000)
    print("\n[3] Committing pilot import in transactional batches (batch_size=2000, limit=5000)...")
    async with AsyncSessionLocal() as session:
        import_summary = await import_indian_catalogue(
            db=session,
            pharmacy_id=pharmacy.id,
            user_id=admin.id,
            csv_path=DATASET_CSV_PATH,
            batch_size=2000,
            limit=5000,
            on_duplicate="skip",
        )
    print(f"    Import summary: {json.dumps(import_summary, indent=2)}")

    # 4. Post-Import Verification & Guard Checks
    print("\n[4] Post-Import Database Verification:")
    async with AsyncSessionLocal() as session:
        med_cnt_post = (await session.execute(select(func.count(Medicine.id)).where(Medicine.pharmacy_id == 1))).scalar()
        mfr_cnt = (await session.execute(select(func.count(Manufacturer.id)).where(Manufacturer.pharmacy_id == 1))).scalar()
        cat_cnt = (await session.execute(select(func.count(Category.id)).where(Category.pharmacy_id == 1))).scalar()
        conv_cnt = (await session.execute(select(func.count(MedicineUnitConversion.id)).where(MedicineUnitConversion.pharmacy_id == 1))).scalar()
        
        batch_cnt = (await session.execute(select(func.count(MedicineBatch.id)).where(MedicineBatch.pharmacy_id == 1))).scalar()
        sm_cnt = (await session.execute(select(func.count(StockMovement.id)).where(StockMovement.pharmacy_id == 1))).scalar()
        discontinued_cnt = (await session.execute(select(func.count(Medicine.id)).where(Medicine.pharmacy_id == 1, Medicine.is_discontinued == True))).scalar()
        mrp_cnt = (await session.execute(select(func.count(Medicine.id)).where(Medicine.pharmacy_id == 1, Medicine.mrp.isnot(None)))).scalar()

        print(f"    Total Medicines in DB: {med_cnt_post}")
        print(f"    Total Manufacturers in DB: {mfr_cnt}")
        print(f"    Total Categories in DB: {cat_cnt}")
        print(f"    Total Unit Conversions in DB: {conv_cnt}")
        print(f"    Medicines with MRP: {mrp_cnt}")
        print(f"    Discontinued Medicines: {discontinued_cnt}")
        print(f"    Batches in DB: {batch_cnt} (MUST BE 0)")
        print(f"    Stock Movements in DB: {sm_cnt} (MUST BE 0)")

        if batch_cnt != 0 or sm_cnt != 0:
            print("CRITICAL ALERT: Stock or batches created during catalogue import!")
            sys.exit(1)

        # 5. Inspect Sample Records across Dosage Forms & Manufacturers
        print("\n[5] Inspecting Sample Imported Records:")
        sample_q = await session.execute(
            select(Medicine, Manufacturer.name.label("mfr_name"), Category.name.label("cat_name"))
            .outerjoin(Manufacturer, Medicine.manufacturer_id == Manufacturer.id)
            .outerjoin(Category, Medicine.category_id == Category.id)
            .where(Medicine.pharmacy_id == 1)
            .order_by(Medicine.id.asc())
            .limit(10)
        )
        for med, mfr_name, cat_name in sample_q.all():
            print(f"    ID: {med.id} | {med.name}")
            print(f"      Brand: {med.brand_name} | Strength: {med.strength} | Form: {med.dosage_form}")
            print(f"      Salt: {med.generic_name}")
            print(f"      Mfr: {mfr_name} | Category: {cat_name}")
            print(f"      MRP: ₹{med.mrp} | Discontinued: {med.is_discontinued} | Pack: {med.pack_size_label}")
            print(f"      Source: {med.source_name} (ID: {med.source_id})")
            print()

        # Check different dosage forms
        print("    Sample representations by Dosage Form:")
        forms = ["Tablet", "Capsule", "Syrup", "Suspension", "Injection", "Ointment", "Drops", "Gel"]
        for form in forms:
            res = (await session.execute(
                select(Medicine.name, Medicine.dosage_form, Medicine.mrp, Medicine.pack_size_label)
                .where(Medicine.pharmacy_id == 1, Medicine.dosage_form == form)
                .limit(1)
            )).first()
            if res:
                print(f"      - {form}: {res.name} (MRP: ₹{res.mrp}, Pack: {res.pack_size_label})")

        # 6. Test Searches (Requirement 8)
        print("\n[6] Testing Searches via Medicine Search Query Logic:")
        
        async def test_search(query_str: str):
            words = [w for w in query_str.strip().split()[:6] if w]
            q = (
                select(Medicine.id, Medicine.name, Medicine.generic_name, Medicine.strength, Medicine.dosage_form, Medicine.mrp, Medicine.is_discontinued, Manufacturer.name.label("mfr_name"))
                .outerjoin(Manufacturer, Medicine.manufacturer_id == Manufacturer.id)
                .where(Medicine.pharmacy_id == 1, Medicine.is_active == True)
            )
            for w in words:
                s = f"%{w}%"
                q = q.where(
                    (Medicine.name.ilike(s)) |
                    (Medicine.brand_name.ilike(s)) |
                    (Medicine.generic_name.ilike(s)) |
                    (Medicine.strength.ilike(s)) |
                    (Medicine.dosage_form.ilike(s)) |
                    (Manufacturer.name.ilike(s))
                )
            q = q.limit(5)
            hits = (await session.execute(q)).all()
            print(f"    Query: '{query_str}' -> {len(hits)} hit(s)")
            for h in hits:
                disc_flag = " [DISCONTINUED]" if h.is_discontinued else ""
                print(f"      * [{h.id}] {h.name} | Generic: {h.generic_name} | Mfr: {h.mfr_name} | MRP: ₹{h.mrp}{disc_flag}")
            return hits

        # A. Brand name
        await test_search("Augmentin")
        await test_search("Azithral")
        await test_search("Allegra")

        # B. Salt / Composition
        await test_search("Amoxycillin Clavulanic")
        await test_search("Fexofenadine")
        await test_search("Ambroxol")

        # C. Strength
        await test_search("625 Duo")
        await test_search("500")

        # D. Manufacturer
        await test_search("Glaxo SmithKline")
        await test_search("Cipla")
        await test_search("Alembic")

        # E. "Dolo 650"
        print("\n    Testing 'Dolo 650':")
        dolo_hits = await test_search("Dolo 650")
        if not dolo_hits:
            print("    Note: 'Dolo 650' was not in the first 5,000 records (which covers the 'A' alphabetical slice of the 254k dataset).")

    print("\n" + "=" * 70)
    print("STEP 2 PILOT SCRIPT FINISHED SUCCESSFULLY")
    print("=" * 70)

if __name__ == "__main__":
    asyncio.run(run_pilot())
