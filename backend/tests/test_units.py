"""
Unit tests for Packaging Units & Conversions.
"""
from decimal import Decimal
import pytest

from app.services.units import UnitConversionService


def test_conversion_to_base_units():
    """1 strip = 10 tablets. 5 strips = 50 tablets."""
    res = UnitConversionService.to_base_units(Decimal("5"), Decimal("10.0000"))
    assert res == Decimal("50.00")


def test_conversion_from_base_units():
    """50 tablets / 10 tablets per strip = 5.0000 strips."""
    res = UnitConversionService.from_base_units(Decimal("50.00"), Decimal("10.0000"))
    assert res == Decimal("5.0000")


def test_fractional_units_allowed():
    """Fluids measured in ml allow fractional quantities."""
    assert UnitConversionService.validate_whole_units(Decimal("2.50"), is_fractional_allowed=True) is True
    assert UnitConversionService.validate_whole_units(Decimal("100.00"), is_fractional_allowed=True) is True


def test_indivisible_whole_units_restriction():
    """Tablets, capsules, and ampoules cannot be split into decimal quantities."""
    assert UnitConversionService.validate_whole_units(Decimal("5.00"), is_fractional_allowed=False) is True
    assert UnitConversionService.validate_whole_units(Decimal("5.50"), is_fractional_allowed=False) is False
    assert UnitConversionService.validate_whole_units(Decimal("0.75"), is_fractional_allowed=False) is False


def test_allocated_acquisition_cost_with_free_goods():
    """
    Supplier promotion:
      Buy 10 strips @ Rs 90/strip, get 2 strips free.
      Total packs = 12.
      Invoice cost = 900.
      Allocated cost per strip = 900 / 12 = Rs 75.0000.
      1 strip = 10 tablets.
      Cost per tablet = 75 / 10 = Rs 7.5000 / tablet.
    """
    res = UnitConversionService.calculate_allocated_cost(
        paid_quantity=Decimal("10.00"),
        free_quantity=Decimal("2.00"),
        unit_purchase_rate=Decimal("90.00"),
        conversion_factor=Decimal("10.0000"),
    )
    assert res["total_quantity_received"] == Decimal("12.00")
    assert res["total_cost"] == Decimal("900.00")
    assert res["effective_cost_per_pack"] == Decimal("75.0000")
    assert res["total_base_units_received"] == Decimal("120.00")
    assert res["cost_per_base_unit"] == Decimal("7.5000")


def test_invalid_conversion_factor_raises():
    """Conversion factor <= 0 must be rejected."""
    with pytest.raises(ValueError):
        UnitConversionService.to_base_units(Decimal("10"), Decimal("0.0000"))
    with pytest.raises(ValueError):
        UnitConversionService.to_base_units(Decimal("10"), Decimal("-5.0000"))
