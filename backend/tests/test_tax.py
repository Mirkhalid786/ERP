"""
Unit tests for GST Tax Calculation Service.
"""
from decimal import Decimal
import pytest

from app.services.tax import GstTaxCalculationService


def test_tax_exclusive_intra_state_12_pct():
    """Test 12% GST on taxable base value 1000.00 (Intra-state: 6% CGST + 6% SGST)."""
    res = GstTaxCalculationService.calculate_tax(
        amount=Decimal("1000.00"),
        rate_percentage=Decimal("12.00"),
        is_tax_inclusive=False,
        is_inter_state=False,
    )
    assert res["taxable_value"] == Decimal("1000.00")
    assert res["cgst_amount"] == Decimal("60.00")
    assert res["sgst_amount"] == Decimal("60.00")
    assert res["igst_amount"] == Decimal("0.00")
    assert res["total_tax"] == Decimal("120.00")
    assert res["total_amount"] == Decimal("1120.00")


def test_tax_exclusive_inter_state_18_pct():
    """Test 18% GST on taxable base value 500.00 (Inter-state: 18% IGST)."""
    res = GstTaxCalculationService.calculate_tax(
        amount=Decimal("500.00"),
        rate_percentage=Decimal("18.00"),
        is_tax_inclusive=False,
        is_inter_state=True,
    )
    assert res["taxable_value"] == Decimal("500.00")
    assert res["cgst_amount"] == Decimal("0.00")
    assert res["sgst_amount"] == Decimal("0.00")
    assert res["igst_amount"] == Decimal("90.00")
    assert res["total_tax"] == Decimal("90.00")
    assert res["total_amount"] == Decimal("590.00")


def test_tax_inclusive_intra_state_12_pct():
    """
    Test 12% GST reverse extraction from MRP 112.00.
    Taxable = 112 / 1.12 = 100.00
    Total Tax = 12.00 (CGST 6.00 + SGST 6.00).
    """
    res = GstTaxCalculationService.calculate_tax(
        amount=Decimal("112.00"),
        rate_percentage=Decimal("12.00"),
        is_tax_inclusive=True,
        is_inter_state=False,
    )
    assert res["total_amount"] == Decimal("112.00")
    assert res["taxable_value"] == Decimal("100.00")
    assert res["cgst_amount"] == Decimal("6.00")
    assert res["sgst_amount"] == Decimal("6.00")
    assert res["igst_amount"] == Decimal("0.00")
    assert res["total_tax"] == Decimal("12.00")


def test_tax_inclusive_rounding_half_up():
    """
    Test fractional MRP reverse extraction with odd cents:
    MRP 150.00 at 5% GST.
    Divisor = 1.05.
    150 / 1.05 = 142.857142... -> rounded to 142.86.
    Tax = 7.14 -> CGST 3.57 + SGST 3.57.
    """
    res = GstTaxCalculationService.calculate_tax(
        amount=Decimal("150.00"),
        rate_percentage=Decimal("5.00"),
        is_tax_inclusive=True,
        is_inter_state=False,
    )
    assert res["total_amount"] == Decimal("150.00")
    assert res["total_tax"] == Decimal("7.14")
    assert res["cgst_amount"] + res["sgst_amount"] == Decimal("7.14")
    assert res["taxable_value"] == Decimal("142.86")


def test_zero_rate_exempt():
    """Test 0% rate / Exempt medicines."""
    res = GstTaxCalculationService.calculate_tax(
        amount=Decimal("250.00"),
        rate_percentage=Decimal("0.00"),
        is_tax_inclusive=True,
        is_inter_state=False,
    )
    assert res["taxable_value"] == Decimal("250.00")
    assert res["total_tax"] == Decimal("0.00")
    assert res["total_amount"] == Decimal("250.00")
