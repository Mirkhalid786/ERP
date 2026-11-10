"""
Direct Supplier Invoice Purchase Service.

Unifies supplier bill entry, inventory receipt, batch creation,
costing, supplier payable and GL posting into ONE primary transaction.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import List, Optional, Tuple, Dict, Any

from fastapi import HTTPException, status
from sqlalchemy import select, func, and_, or_, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.clock import today_local
from app.models.auth import Pharmacy, AuditLog, User
from app.models.inventory import (
    Medicine, MedicineBatch, StockMovement, MedicineUnitConversion,
    Unit, TaxRate, utcnow,
)
from app.models.purchasing import (
    Supplier, PurchaseOrder, PurchaseOrderItem,
    GoodsReceipt, GoodsReceiptItem,
    SupplierInvoice, SupplierInvoiceItem,
    SupplierReturn,
)
from app.models.payables import SupplierPaymentAllocation, SupplierCreditNote
from app.schemas.purchasing import (
    DirectPurchaseCreatePayload,
    DirectPurchaseCancelPayload,
    DirectPurchaseItemPayload,
)
from app.services.purchasing import (
    SupplierService,
    GoodsReceiptService,
    SupplierInvoiceService,
    PurchaseOrderService,
    invoice_number_key,
    round_cur,
    round_four,
    normalize_state_code,
    UnitConversionService,
    GstTaxCalculationService,
    TWO_PLACES,
)
from app.services import supplier_invoices, gl_posting


class DirectPurchaseService:
    @staticmethod
    async def create_or_post_purchase(
        db: AsyncSession,
        pharmacy_id: int,
        payload: DirectPurchaseCreatePayload,
        user: User,
        perms: set,
    ) -> SupplierInvoice:
        """
        Primary Direct Purchase transaction:
          - If post_now=True: Atomically records bill, creates & posts internal GRN,
            creates batches, updates stock ledger, finalizes invoice, records payable and GL entries.
          - If post_now=False: Saves invoice and internal GRN as DRAFT (zero stock / GL mutation).
        """
        supplier = await SupplierService.get_supplier(db, pharmacy_id, payload.supplier_id)
        if not supplier.is_active:
            raise HTTPException(status_code=400, detail="Cannot record purchase for inactive supplier")

        inv_num_clean = payload.invoice_number.strip().upper()
        number_key = invoice_number_key(inv_num_clean, payload.invoice_date)
        await SupplierInvoiceService.assert_number_free(db, pharmacy_id, supplier, number_key)

        # State code normalization for GST
        pharmacy_res = await db.execute(select(Pharmacy).where(Pharmacy.id == pharmacy_id))
        pharmacy = pharmacy_res.scalar_one()
        pharm_state = normalize_state_code(pharmacy.state_code, pharmacy.gstin)
        supp_state = normalize_state_code(supplier.state_code, supplier.gstin)
        is_inter_state = bool(pharm_state and supp_state and pharm_state != supp_state)

        # Due date
        due_date = payload.due_date
        if not due_date:
            due_date = payload.invoice_date + timedelta(days=payload.payment_terms_days)

        # Optional advance PO
        po: Optional[PurchaseOrder] = None
        if payload.po_id:
            po = await PurchaseOrderService.get_purchase_order(db, pharmacy_id, payload.po_id)
            if po.supplier_id != supplier.id:
                raise HTTPException(status_code=400, detail="Purchase order does not belong to this supplier")

        today = today_local()

        # Step 1: Create GoodsReceipt (DRAFT initially)
        grn_number = await GoodsReceiptService.generate_grn_number(db, pharmacy_id)
        grn = GoodsReceipt(
            pharmacy_id=pharmacy_id,
            grn_number=grn_number,
            supplier_id=supplier.id,
            po_id=po.id if po else None,
            receipt_date=payload.invoice_date,
            supplier_challan_no=None,
            supplier_invoice_no=inv_num_clean,
            status="DRAFT",
            notes=payload.notes,
            received_by=user.id,
        )
        db.add(grn)
        await db.flush()

        grn_items: List[GoodsReceiptItem] = []
        total_received_base = Decimal("0.00")
        total_accepted_base = Decimal("0.00")
        total_free_base = Decimal("0.00")
        grn_subtotal = Decimal("0.00")
        grn_tax_total = Decimal("0.00")

        # Step 2: Create GoodsReceiptItems
        for it in payload.items:
            # Validate Medicine
            med_res = await db.execute(
                select(Medicine).options(selectinload(Medicine.base_unit)).where(
                    Medicine.id == it.medicine_id,
                    Medicine.pharmacy_id == pharmacy_id,
                )
            )
            medicine = med_res.scalar_one_or_none()
            if not medicine:
                raise HTTPException(status_code=404, detail=f"Medicine ID {it.medicine_id} not found")

            # Validate Expiry
            if it.expiry_date <= today:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Medicine '{medicine.name}' batch '{it.batch_number}' has expired ({it.expiry_date}). Expired stock cannot be received into inventory.",
                )

            # Selling rate / MRP compliance
            selling_rate = it.selling_rate or min(it.mrp, round_cur(it.purchase_rate * Decimal("1.25")))
            if selling_rate > it.mrp:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Selling rate ({selling_rate}) cannot exceed Maximum Retail Price (MRP {it.mrp}) for '{medicine.name}'.",
                )

            # Packaging unit conversion
            conversion_factor = Decimal("1.0000")
            if it.packaging_unit_id != medicine.base_unit_id:
                conv_res = await db.execute(
                    select(MedicineUnitConversion).where(
                        MedicineUnitConversion.medicine_id == medicine.id,
                        MedicineUnitConversion.packaging_unit_id == it.packaging_unit_id,
                    )
                )
                conv = conv_res.scalar_one_or_none()
                if not conv:
                    raise HTTPException(
                        status_code=422,
                        detail=f"Unit conversion not configured for packaging unit ID {it.packaging_unit_id} on '{medicine.name}'",
                    )
                conversion_factor = conv.conversion_factor

            rec_pack = it.quantity
            free_pack = it.free_quantity
            rej_pack = Decimal("0.00")
            accepted_pack = rec_pack + free_pack

            accepted_qty_base = UnitConversionService.to_base_units(accepted_pack, conversion_factor)
            received_qty_base = UnitConversionService.to_base_units(rec_pack, conversion_factor)
            free_qty_base = UnitConversionService.to_base_units(free_pack, conversion_factor)

            # Allocated acquisition cost per base unit accounting for free goods
            allocated = UnitConversionService.calculate_allocated_cost(
                paid_quantity=rec_pack,
                free_quantity=free_pack,
                unit_purchase_rate=it.purchase_rate,
                conversion_factor=conversion_factor,
                discount_percent=it.discount_percent,
            )
            cost_per_base_unit = allocated["cost_per_base_unit"]

            # Line financial amounts
            gross_line = round_cur(rec_pack * it.purchase_rate)
            disc_line = round_cur(gross_line * (it.discount_percent / Decimal("100.00")))
            taxable_line = gross_line - disc_line

            cgst_amt = Decimal("0.00")
            sgst_amt = Decimal("0.00")
            igst_amt = Decimal("0.00")
            tax_amount = Decimal("0.00")

            if it.tax_rate_id:
                tr_res = await db.execute(
                    select(TaxRate).where(TaxRate.id == it.tax_rate_id, TaxRate.pharmacy_id == pharmacy_id)
                )
                tax_rate = tr_res.scalar_one_or_none()
                if tax_rate:
                    tax_calc = GstTaxCalculationService.calculate_tax(
                        amount=taxable_line,
                        rate_percentage=tax_rate.rate_percentage,
                        is_tax_inclusive=payload.is_tax_inclusive,
                        is_inter_state=is_inter_state,
                    )
                    cgst_amt = tax_calc["cgst_amount"]
                    sgst_amt = tax_calc["sgst_amount"]
                    igst_amt = tax_calc["igst_amount"]
                    tax_amount = tax_calc["total_tax"]
                    if payload.is_tax_inclusive:
                        taxable_line = tax_calc["taxable_value"]

            line_total = taxable_line + tax_amount

            total_received_base += received_qty_base
            total_accepted_base += accepted_qty_base
            total_free_base += free_qty_base
            grn_subtotal += taxable_line
            grn_tax_total += tax_amount

            # Match PO item if applicable
            po_item_id = None
            if po and po.items:
                po_item_match = next((p for p in po.items if p.medicine_id == medicine.id), None)
                if po_item_match:
                    po_item_id = po_item_match.id

            grn_item = GoodsReceiptItem(
                grn_id=grn.id,
                po_item_id=po_item_id,
                medicine_id=medicine.id,
                batch_number=it.batch_number.strip().upper(),
                manufacturing_date=it.manufacturing_date,
                expiry_date=it.expiry_date,
                packaging_unit_id=it.packaging_unit_id,
                conversion_factor=conversion_factor,
                received_qty_pack=rec_pack,
                free_qty_pack=free_pack,
                rejected_qty_pack=rej_pack,
                accepted_qty_base=accepted_qty_base,
                rejected_qty_base=Decimal("0.00"),
                rejection_reason=None,
                purchase_rate=it.purchase_rate,
                discount_percent=it.discount_percent,
                mrp=it.mrp,
                selling_rate=selling_rate,
                cost_per_base_unit=cost_per_base_unit,
                tax_rate_id=it.tax_rate_id,
                cgst_amount=cgst_amt,
                sgst_amount=sgst_amt,
                igst_amount=igst_amt,
                tax_amount=tax_amount,
                line_total=line_total,
                rack_location=it.rack_location.strip() if it.rack_location else None,
            )
            db.add(grn_item)
            grn_items.append(grn_item)

        grn.total_received_base = round_cur(total_received_base)
        grn.total_accepted_base = round_cur(total_accepted_base)
        grn.total_rejected_base = Decimal("0.00")
        grn.total_free_base = round_cur(total_free_base)
        grn.subtotal = round_cur(grn_subtotal)
        grn.tax_total = round_cur(grn_tax_total)
        grn.grand_total = round_cur(grn_subtotal + grn_tax_total)
        await db.flush()

        # Step 3: Create SupplierInvoice
        calc_grand = round_cur(grn.grand_total + payload.other_charges + payload.round_off)

        invoice = SupplierInvoice(
            pharmacy_id=pharmacy_id,
            invoice_number=inv_num_clean,
            number_key=number_key,
            supplier_id=supplier.id,
            grn_id=grn.id,
            po_id=po.id if po else None,
            invoice_date=payload.invoice_date,
            received_date=payload.invoice_date,
            due_date=due_date,
            payment_terms_days=payload.payment_terms_days,
            is_tax_inclusive=payload.is_tax_inclusive,
            currency="INR",
            other_charges=payload.other_charges,
            round_off=payload.round_off,
            calculated_grand_total=calc_grand,
            supplier_declared_total=payload.supplier_declared_total or calc_grand,
            discrepancy_amount=Decimal("0.00"),
            discrepancy_notes=None,
            notes=payload.notes,
            status="DRAFT",
            payment_status="UNPAID",
            paid_amount=Decimal("0.00"),
            created_by=user.id,
        )
        db.add(invoice)
        await db.flush()

        inv_subtotal = Decimal("0.00")
        inv_discount_total = Decimal("0.00")
        inv_cgst_total = Decimal("0.00")
        inv_sgst_total = Decimal("0.00")
        inv_igst_total = Decimal("0.00")
        inv_tax_total = Decimal("0.00")

        # Step 4: Create SupplierInvoiceItems linked to GoodsReceiptItems
        created_inv_items = []
        for gi in grn_items:
            gross = round_cur(gi.received_qty_pack * gi.purchase_rate)
            disc = round_cur(gross * (gi.discount_percent / Decimal("100.00")))
            inv_subtotal += gross
            inv_discount_total += disc
            inv_cgst_total += gi.cgst_amount
            inv_sgst_total += gi.sgst_amount
            inv_igst_total += gi.igst_amount
            inv_tax_total += gi.tax_amount

            inv_item = SupplierInvoiceItem(
                invoice_id=invoice.id,
                grn_item_id=gi.id,
                medicine_id=gi.medicine_id,
                batch_number=gi.batch_number,
                packaging_unit_id=gi.packaging_unit_id,
                conversion_factor=gi.conversion_factor,
                billed_qty_pack=gi.received_qty_pack,
                billed_qty_base=round_cur(gi.received_qty_pack * gi.conversion_factor),
                free_qty_pack=gi.free_qty_pack,
                purchase_rate=gi.purchase_rate,
                discount_percent=gi.discount_percent,
                discount_amount=disc,
                taxable_amount=gi.line_total - gi.tax_amount,
                tax_rate_id=gi.tax_rate_id,
                cgst_amount=gi.cgst_amount,
                sgst_amount=gi.sgst_amount,
                igst_amount=gi.igst_amount,
                tax_amount=gi.tax_amount,
                line_total=gi.line_total,
            )
            db.add(inv_item)
            created_inv_items.append((gi, inv_item))

        invoice.subtotal = round_cur(inv_subtotal)
        invoice.discount_total = round_cur(inv_discount_total)
        invoice.cgst_total = round_cur(inv_cgst_total)
        invoice.sgst_total = round_cur(inv_sgst_total)
        invoice.igst_total = round_cur(inv_igst_total)
        invoice.tax_total = round_cur(inv_tax_total)
        await db.flush()

        # Step 5: If post_now=True, post GRN and Finalize Invoice in the same transaction
        if payload.post_now:
            # 5a. Post Goods Receipt (mutates stock, creates batches, posts GL GOODS_RECEIPT)
            await GoodsReceiptService.post_goods_receipt(db, pharmacy_id, grn.id, user.id)

            # 5b. Update invoice item batch_ids from posted GRN items
            for gi, inv_item in created_inv_items:
                inv_item.batch_id = gi.batch_id
            await db.flush()

            # 5c. Finalize Supplier Invoice (creates payable liability, posts GL SUPPLIER_BILL)
            await supplier_invoices.finalize(db, user, perms, invoice.id, acknowledge_warnings=True)

            # Audit log for direct purchase
            db.add(AuditLog(
                pharmacy_id=pharmacy_id,
                user_id=user.id,
                action="purchase.direct_post",
                entity_type="supplier_invoice",
                entity_id=str(invoice.id),
                metadata_json=str({
                    "invoice_number": invoice.invoice_number,
                    "supplier_id": supplier.id,
                    "grn_number": grn.grn_number,
                    "grand_total": str(invoice.calculated_grand_total),
                    "items_count": len(payload.items),
                }),
            ))
            await db.flush()
        else:
            # Draft purchase audit log
            db.add(AuditLog(
                pharmacy_id=pharmacy_id,
                user_id=user.id,
                action="purchase.direct_draft",
                entity_type="supplier_invoice",
                entity_id=str(invoice.id),
                metadata_json=str({
                    "invoice_number": invoice.invoice_number,
                    "supplier_id": supplier.id,
                    "grand_total": str(invoice.calculated_grand_total),
                }),
            ))
            await db.flush()

        return await SupplierInvoiceService.get_invoice(db, pharmacy_id, invoice.id)

    @staticmethod
    async def post_draft_purchase(
        db: AsyncSession,
        pharmacy_id: int,
        invoice_id: int,
        user: User,
        perms: set,
    ) -> SupplierInvoice:
        """Post an existing draft direct purchase into stock and supplier payable."""
        invoice = await SupplierInvoiceService.get_invoice(db, pharmacy_id, invoice_id)
        if invoice.status != "DRAFT":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Only draft purchases can be posted; this one is {invoice.status.lower()}",
            )

        if not invoice.grn_id:
            raise HTTPException(status_code=400, detail="Draft purchase has no associated goods receipt")

        grn = await GoodsReceiptService.get_goods_receipt(db, pharmacy_id, invoice.grn_id)
        if grn.status == "DRAFT":
            await GoodsReceiptService.post_goods_receipt(db, pharmacy_id, grn.id, user.id)

            # Update batch_ids on invoice lines
            for gi in grn.items:
                inv_match = next((ii for ii in invoice.items if ii.grn_item_id == gi.id), None)
                if inv_match:
                    inv_match.batch_id = gi.batch_id
            await db.flush()

        await supplier_invoices.finalize(db, user, perms, invoice.id, acknowledge_warnings=True)

        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user.id,
            action="purchase.draft_post",
            entity_type="supplier_invoice",
            entity_id=str(invoice.id),
            metadata_json=str({"invoice_number": invoice.invoice_number}),
        ))
        await db.flush()
        return await SupplierInvoiceService.get_invoice(db, pharmacy_id, invoice.id)

    @staticmethod
    async def delete_draft_purchase(
        db: AsyncSession,
        pharmacy_id: int,
        invoice_id: int,
        user: User,
    ) -> dict:
        """Deletes a draft purchase and its draft goods receipt. Zero stock/GL impact."""
        invoice = await SupplierInvoiceService.get_invoice(db, pharmacy_id, invoice_id)
        if invoice.status != "DRAFT":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Only draft purchases can be deleted; this one is {invoice.status.lower()}",
            )

        grn_id = invoice.grn_id
        inv_number = invoice.invoice_number

        # Delete invoice lines and invoice
        await db.execute(delete(SupplierInvoiceItem).where(SupplierInvoiceItem.invoice_id == invoice.id))
        await db.delete(invoice)
        await db.flush()

        # Delete linked draft GRN
        if grn_id:
            grn = (await db.execute(
                select(GoodsReceipt).where(GoodsReceipt.id == grn_id, GoodsReceipt.pharmacy_id == pharmacy_id)
            )).scalar_one_or_none()
            if grn and grn.status == "DRAFT":
                await db.execute(delete(GoodsReceiptItem).where(GoodsReceiptItem.grn_id == grn.id))
                await db.delete(grn)
                await db.flush()

        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user.id,
            action="purchase.draft_delete",
            entity_type="supplier_invoice",
            entity_id=str(invoice_id),
            metadata_json=str({"invoice_number": inv_number}),
        ))
        await db.flush()
        return {"success": True, "message": f"Draft purchase '{inv_number}' deleted successfully"}

    @staticmethod
    async def cancel_posted_purchase(
        db: AsyncSession,
        pharmacy_id: int,
        invoice_id: int,
        payload: DirectPurchaseCancelPayload,
        user: User,
        perms: set,
    ) -> SupplierInvoice:
        """
        Controlled cancellation of a posted purchase:
          - If ANY stock has been sold/consumed: BLOCKED! Instructs to use Returns/Credit Notes.
          - If all stock remains: Atomically reverses stock, writes PURCHASE_CANCEL movement,
            cancels invoice, cancels GRN, and reverses GL entries.
        """
        invoice = await SupplierInvoiceService.get_invoice(db, pharmacy_id, invoice_id)
        if invoice.status != "POSTED":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot cancel invoice with status '{invoice.status}'. Only POSTED purchases can be cancelled.",
            )

        # Check payments allocated
        allocated = (await db.execute(
            select(func.coalesce(func.sum(SupplierPaymentAllocation.amount), Decimal("0.00"))).where(
                SupplierPaymentAllocation.invoice_id == invoice.id,
                SupplierPaymentAllocation.status == "ACTIVE",
            )
        )).scalar_one()
        if allocated > Decimal("0.00"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"₹{allocated} of supplier payments is allocated to this invoice. Reverse those payments first.",
            )

        # Check active returns
        returns = (await db.execute(
            select(SupplierReturn.return_number).where(
                SupplierReturn.invoice_id == invoice.id,
                SupplierReturn.status == "POSTED",
            )
        )).scalars().all()
        if returns:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Posted supplier returns refer to this invoice: {', '.join(returns)}",
            )

        # Check stock consumption for each line
        items = list(invoice.items or [])
        batches_to_reverse = []

        for item in items:
            if item.batch_id:
                batch_q = select(MedicineBatch).where(
                    MedicineBatch.id == item.batch_id,
                    MedicineBatch.pharmacy_id == pharmacy_id,
                ).with_for_update()
                batch = (await db.execute(batch_q)).scalar_one_or_none()

                if batch is not None:
                    # The batch received billed + free quantities; cancellation
                    # must reverse (and therefore require) BOTH, otherwise free
                    # stock would survive a cancelled purchase with no backing.
                    received_base = (
                        item.billed_qty_base + item.free_qty_pack * item.conversion_factor
                    ).quantize(TWO_PLACES)
                    if batch.current_stock_base < received_base:
                        med_res = await db.execute(select(Medicine).where(Medicine.id == item.medicine_id))
                        med = med_res.scalar_one()
                        raise HTTPException(
                            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=(
                                f"Cannot cancel purchase '{invoice.invoice_number}': stock for medicine '{med.name}' "
                                f"(batch '{item.batch_number}') has already been sold or consumed "
                                f"(current stock: {batch.current_stock_base}, received: {received_base}). "
                                f"The ERP prevents negative inventory. "
                                f"Please record a Purchase Return or Supplier Credit Note for eligible remaining stock."
                            ),
                        )
                    batches_to_reverse.append((batch, item, received_base))

        # All stock intact -> reverse stock movements atomically
        for batch, item, received_base in batches_to_reverse:
            batch.current_stock_base = (batch.current_stock_base - received_base).quantize(TWO_PLACES)
            movement = StockMovement(
                pharmacy_id=pharmacy_id,
                medicine_id=item.medicine_id,
                batch_id=batch.id,
                movement_type="PURCHASE_CANCEL",
                reference_id=invoice.invoice_number,
                reference_type="purchase_cancel",
                quantity_base=-received_base,
                balance_after_base=batch.current_stock_base,
                unit_cost=batch.cost_per_base_unit,
                total_cost=round_cur(received_base * batch.cost_per_base_unit),
                reason=f"Purchase Cancellation: {payload.reason.strip()}",
                notes=f"Reversal of purchase bill {invoice.invoice_number}",
                user_id=user.id,
            )
            db.add(movement)

        # Cancel linked Goods Receipt
        if invoice.grn_id:
            grn_q = select(GoodsReceipt).where(
                GoodsReceipt.id == invoice.grn_id,
                GoodsReceipt.pharmacy_id == pharmacy_id,
            ).with_for_update()
            grn = (await db.execute(grn_q)).scalar_one_or_none()
            if grn:
                grn.status = "CANCELLED"
                grn.updated_at = utcnow()
                await gl_posting.record(db, pharmacy_id, user.id, "GOODS_RECEIPT_CANCEL", grn.id)

        # Cancel Supplier Invoice via established workflow
        await supplier_invoices.cancel(db, user, perms, invoice.id, payload.reason.strip())

        db.add(AuditLog(
            pharmacy_id=pharmacy_id,
            user_id=user.id,
            action="purchase.direct_cancel",
            entity_type="supplier_invoice",
            entity_id=str(invoice.id),
            reason=payload.reason.strip(),
            metadata_json=str({
                "invoice_number": invoice.invoice_number,
                "reason": payload.reason.strip(),
            }),
        ))
        await db.flush()
        return await SupplierInvoiceService.get_invoice(db, pharmacy_id, invoice.id)
