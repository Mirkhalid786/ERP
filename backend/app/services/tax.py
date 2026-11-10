"""
GST Tax Calculation Service for Indian Retail Pharmacy ERP.

Tax Calculation Assumptions and Legal Considerations:
1. Dual GST Architecture:
   - Intra-state transactions (supplier and buyer in the same Indian State/UT) attract CGST + SGST in equal halves (e.g., a 12% rate splits into 6% CGST + 6% SGST).
   - Inter-state transactions attract IGST at the full rate (e.g., 12% IGST).
2. Calculation Modes:
   - Tax-Exclusive: Common in purchase invoices and wholesale sales. Tax is added on top of the base taxable value.
   - Tax-Inclusive: Standard for Indian retail pharmacy counter sales (OTC/prescriptions) where medicines have printed Maximum Retail Price (MRP) which is legally inclusive of all taxes under the Legal Metrology Act and DPCO (Drugs Price Control Order).
3. Rounding & Precision:
   - All arithmetic uses Python's Decimal with ROUND_HALF_UP.
   - Taxable value and individual taxes are rounded to 2 decimal places.
4. Validation Required by an Indian Tax Professional / CA:
   - HSN codes (e.g. Chapter 3004) and specific exemptions (e.g. life-saving drugs under concessional notifications, insulin, dialysis fluids).
   - Rounding off under Section 170 of the CGST Act (rounding to the nearest rupee on final invoice totals vs line-item tax calculation).
   - Composition scheme pharmacies (if applicable in future, where tax is not collected separately from retail buyers).
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional

TWO_PLACES = Decimal("0.01")
FOUR_PLACES = Decimal("0.0001")


def round_currency(value: Decimal) -> Decimal:
    """Round a monetary amount to 2 decimal places using standard ROUND_HALF_UP."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


class GstTaxCalculationService:
    @staticmethod
    def calculate_tax(
        amount: Decimal,
        rate_percentage: Decimal,
        is_tax_inclusive: bool = True,
        is_inter_state: bool = False,
        cgst_pct_override: Optional[Decimal] = None,
        sgst_pct_override: Optional[Decimal] = None,
        igst_pct_override: Optional[Decimal] = None,
    ) -> Dict[str, Any]:
        """
        Calculates GST breakdown for an amount.

        Args:
            amount: The monetary amount (MRP if inclusive, base price if exclusive).
            rate_percentage: Total GST rate percentage (e.g. 12.00).
            is_tax_inclusive: True if amount includes GST (MRP retail mode).
            is_inter_state: True if inter-state (IGST), False if intra-state (CGST + SGST).
            cgst_pct_override: Optional explicit CGST rate.
            sgst_pct_override: Optional explicit SGST rate.
            igst_pct_override: Optional explicit IGST rate.

        Returns:
            Dict containing taxable_value, cgst_amount, sgst_amount, igst_amount, total_tax, total_amount.
        """
        rate_pct = Decimal(str(rate_percentage))
        amt = Decimal(str(amount))

        # Determine individual rate percentages
        if is_inter_state:
            igst_pct = igst_pct_override if igst_pct_override is not None else rate_pct
            cgst_pct = Decimal("0.00")
            sgst_pct = Decimal("0.00")
        else:
            half_rate = rate_pct / Decimal("2")
            cgst_pct = cgst_pct_override if cgst_pct_override is not None else half_rate
            sgst_pct = sgst_pct_override if sgst_pct_override is not None else half_rate
            igst_pct = Decimal("0.00")

        if rate_pct == Decimal("0.00"):
            return {
                "taxable_value": round_currency(amt),
                "cgst_percentage": Decimal("0.00"),
                "cgst_amount": Decimal("0.00"),
                "sgst_percentage": Decimal("0.00"),
                "sgst_amount": Decimal("0.00"),
                "igst_percentage": Decimal("0.00"),
                "igst_amount": Decimal("0.00"),
                "total_tax": Decimal("0.00"),
                "total_amount": round_currency(amt),
                "is_tax_inclusive": is_tax_inclusive,
                "is_inter_state": is_inter_state,
            }

        if is_tax_inclusive:
            # Formula: Taxable Value = Total Amount / (1 + (Rate / 100))
            divisor = Decimal("1") + (rate_pct / Decimal("100"))
            taxable_val = amt / divisor
            total_tax_exact = amt - taxable_val
            
            if is_inter_state:
                igst_amt = round_currency(total_tax_exact)
                cgst_amt = Decimal("0.00")
                sgst_amt = Decimal("0.00")
                total_tax = igst_amt
            else:
                cgst_amt = round_currency(total_tax_exact / Decimal("2"))
                # To prevent rounding discrepancy, sgst takes remainder
                sgst_amt = round_currency(total_tax_exact) - cgst_amt
                igst_amt = Decimal("0.00")
                total_tax = cgst_amt + sgst_amt

            taxable_val_rounded = round_currency(amt - total_tax)
            total_amt_rounded = round_currency(amt)

            return {
                "taxable_value": taxable_val_rounded,
                "cgst_percentage": cgst_pct,
                "cgst_amount": cgst_amt,
                "sgst_percentage": sgst_pct,
                "sgst_amount": sgst_amt,
                "igst_percentage": igst_pct,
                "igst_amount": igst_amt,
                "total_tax": total_tax,
                "total_amount": total_amt_rounded,
                "is_tax_inclusive": True,
                "is_inter_state": is_inter_state,
            }
        else:
            # Formula: Tax = Taxable Value * (Rate / 100)
            taxable_val_rounded = round_currency(amt)
            if is_inter_state:
                igst_amt = round_currency(taxable_val_rounded * (igst_pct / Decimal("100")))
                cgst_amt = Decimal("0.00")
                sgst_amt = Decimal("0.00")
                total_tax = igst_amt
            else:
                cgst_amt = round_currency(taxable_val_rounded * (cgst_pct / Decimal("100")))
                sgst_amt = round_currency(taxable_val_rounded * (sgst_pct / Decimal("100")))
                igst_amt = Decimal("0.00")
                total_tax = cgst_amt + sgst_amt

            total_amt_rounded = round_currency(taxable_val_rounded + total_tax)

            return {
                "taxable_value": taxable_val_rounded,
                "cgst_percentage": cgst_pct,
                "cgst_amount": cgst_amt,
                "sgst_percentage": sgst_pct,
                "sgst_amount": sgst_amt,
                "igst_percentage": igst_pct,
                "igst_amount": igst_amt,
                "total_tax": total_tax,
                "total_amount": total_amt_rounded,
                "is_tax_inclusive": False,
                "is_inter_state": is_inter_state,
            }
