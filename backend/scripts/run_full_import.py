"""
Full Indian Medicine Dataset Importer (Phase 2).
Executes streaming transactional batched import of all ~254k records.
Guarantees:
  - Zero duplicate records created (pilot records 1..5000 skipped via cached keys).
  - Purely additive catalogue data (zero batches, zero stock movements, zero accounting entries).
  - Preserves manufacturer mappings, salts, strengths, MRP reference, pack labels, and discontinued status.
"""
import asyncio
import json
import logging
import os
import sys
import time
from decimal import Decimal

from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

# Disable verbose SQL statement logging for bulk import throughput
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

from app.core.database import AsyncSessionLocal, engine
engine.echo = False

from app.models.auth import Pharmacy, User
from app.models.inventory import (
    Medicine, Manufacturer, Category, Unit, MedicineUnitConversion,
    MedicineBatch, StockMovement,
)
from app.services.indian_medicine_import import (
    DATASET_CSV_PATH,
    import_indian_catalogue,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("full_import")


async def run_full_import():
    print("=" * 80)
    print("FULL INDIAN MEDICINE DATASET IMPORT (~254,000 RECORDS)")
    print("=" * 80)
    t0 = time.time()

    async with AsyncSessionLocal() as session:
        # Pre-condition check
        rev = (await session.execute(text("SELECT version_num FROM alembic_version"))).scalar_one_or_none()
        print(f"[1] Database Revision: {rev}")
        if rev != "013":
            print(f"ABORT: Expected revision '013', but found '{rev}'")
            sys.exit(1)

        pharm_res = await session.execute(select(Pharmacy).where(Pharmacy.id == 1))
        pharmacy = pharm_res.scalar_one_or_none()
        admin_res = await session.execute(select(User).where(User.username == "admin"))
        admin = admin_res.scalar_one_or_none()
        if not pharmacy or not admin:
            print("ABORT: Master records missing!")
            sys.exit(1)

        med_pre = (await session.execute(select(func.count(Medicine.id)).where(Medicine.pharmacy_id == 1))).scalar()
        mfr_pre = (await session.execute(select(func.count(Manufacturer.id)).where(Manufacturer.pharmacy_id == 1))).scalar()
        print(f"    Existing State: {med_pre} medicines, {mfr_pre} manufacturers currently in DB.")

    print(f"\n[2] Starting Full Dataset Import from {DATASET_CSV_PATH}...")
    last_log_time = time.time()

    def on_progress(p: dict):
        nonlocal last_log_time
        now = time.time()
        elapsed = now - t0
        proc = p["total_processed"]
        created = p["created_medicines"]
        skipped = p["skipped_duplicates"]
        mfrs = p["created_manufacturers"]
        rate = proc / elapsed if elapsed > 0 else 0
        pct = (proc / 253973) * 100
        # Log every ~5,000 records or 5 seconds
        if proc % 10000 == 0 or (now - last_log_time) >= 5 or proc >= 253900:
            print(f"    [{pct:5.1f}%] Processed: {proc:,}/253,973 | Created: {created:,} | Skipped: {skipped:,} | Mfrs: {mfrs:,} | Speed: {rate:.0f} rows/s | Elapsed: {elapsed:.1f}s")
            last_log_time = now

    async with AsyncSessionLocal() as session:
        summary = await import_indian_catalogue(
            db=session,
            pharmacy_id=pharmacy.id,
            user_id=admin.id,
            csv_path=DATASET_CSV_PATH,
            batch_size=2000,
            limit=None,  # Full dataset
            on_duplicate="skip",
            progress_callback=on_progress,
        )

    duration = time.time() - t0
    print(f"\n[3] Import Finished in {duration:.1f}s ({duration/60:.2f} mins)!")
    print(f"    Summary: {json.dumps(summary, indent=2)}")

    print("\n[4] Comprehensive Database Verification:")
    async with AsyncSessionLocal() as session:
        med_cnt = (await session.execute(select(func.count(Medicine.id)).where(Medicine.pharmacy_id == 1))).scalar()
        mfr_cnt = (await session.execute(select(func.count(Manufacturer.id)).where(Manufacturer.pharmacy_id == 1))).scalar()
        cat_cnt = (await session.execute(select(func.count(Category.id)).where(Category.pharmacy_id == 1))).scalar()
        conv_cnt = (await session.execute(select(func.count(MedicineUnitConversion.id)).where(MedicineUnitConversion.pharmacy_id == 1))).scalar()
        
        batch_cnt = (await session.execute(select(func.count(MedicineBatch.id)).where(MedicineBatch.pharmacy_id == 1))).scalar()
        sm_cnt = (await session.execute(select(func.count(StockMovement.id)).where(StockMovement.pharmacy_id == 1))).scalar()
        discontinued_cnt = (await session.execute(select(func.count(Medicine.id)).where(Medicine.pharmacy_id == 1, Medicine.is_discontinued == True))).scalar()
        mrp_cnt = (await session.execute(select(func.count(Medicine.id)).where(Medicine.pharmacy_id == 1, Medicine.mrp.isnot(None)))).scalar()

        print(f"    Total Medicines in DB: {med_cnt:,}")
        print(f"    Total Manufacturers in DB: {mfr_cnt:,}")
        print(f"    Total Categories in DB: {cat_cnt}")
        print(f"    Total Unit Conversions in DB: {conv_cnt:,}")
        print(f"    Medicines with MRP: {mrp_cnt:,} ({mrp_cnt/med_cnt*100:.1f}%)")
        print(f"    Discontinued Medicines: {discontinued_cnt:,} ({discontinued_cnt/med_cnt*100:.1f}%)")
        print(f"    Active Marketed Medicines: {med_cnt - discontinued_cnt:,}")
        print(f"    Batches in DB: {batch_cnt} (MUST BE 0)")
        print(f"    Stock Movements in DB: {sm_cnt} (MUST BE 0)")

        if batch_cnt != 0 or sm_cnt != 0:
            print("CRITICAL ALERT: Stock or batches created during catalogue import!")
            sys.exit(1)

        # 5. Search Tests Across Brands and Beyond Pilot Range
        print("\n[5] Testing Search for Key Brands Across Full Dataset:")
        
        async def verify_search(query_str: str):
            words = [w for w in query_str.strip().split()[:6] if w]
            q = (
                select(Medicine.id, Medicine.name, Medicine.brand_name, Medicine.generic_name, Medicine.strength, Medicine.dosage_form, Medicine.mrp, Medicine.is_discontinued, Manufacturer.name.label("mfr_name"))
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
            q = q.limit(4)
            hits = (await session.execute(q)).all()
            print(f"\n  * Query '{query_str}' -> {len(hits)} sample hit(s):")
            for h in hits:
                disc = " [DISCONTINUED]" if h.is_discontinued else ""
                print(f"      [{h.id}] {h.name}{disc}")
                print(f"          Generic: {h.generic_name}")
                print(f"          Manufacturer: {h.mfr_name} | Form: {h.dosage_form} | Strength: {h.strength} | MRP: ₹{h.mrp}")
            return hits

        # Dolo 650 specifically
        await verify_search("Dolo 650")
        # Major Indian Brands beyond 'A':
        await verify_search("Crocin 650")
        await verify_search("Calpol 500")
        await verify_search("Combiflam")
        await verify_search("Telma 40")
        await verify_search("Pan 40")
        await verify_search("Glycomet 500")
        await verify_search("Lipaglyn")

    print("\n" + "=" * 80)
    print("FULL IMPORT COMPLETED SUCCESSFULLY")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(run_full_import())
