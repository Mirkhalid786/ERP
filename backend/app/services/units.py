"""
Units and Packaging Conversion Service.

Provides:
  - Conversion between packaging units (Box, Strip, Bottle) and base unit (Tablet, ml, Piece).
  - Validation of conversion factors and whole-unit (indivisible) constraints.
  - Allocated acquisition costing for supplier free quantities.
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any

FOUR_PLACES = Decimal("0.0001")
TWO_PLACES = Decimal("0.01")


class UnitConversionService:
    @staticmethod
    def to_base_units(quantity: Decimal, conversion_factor: Decimal) -> Decimal:
        """
        Convert a quantity of a packaging unit into base units.
        Example: 5 strips * 10 tablets/strip = 50 tablets.
        """
        qty = Decimal(str(quantity))
        factor = Decimal(str(conversion_factor))
        if factor <= Decimal("0"):
            raise ValueError("Conversion factor must be strictly positive")
        return (qty * factor).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)

    @staticmethod
    def from_base_units(base_quantity: Decimal, conversion_factor: Decimal) -> Decimal:
        """
        Convert base units back into packaging units.
        Example: 50 tablets / 10 tablets/strip = 5 strips.
        """
        base_qty = Decimal(str(base_quantity))
        factor = Decimal(str(conversion_factor))
        if factor <= Decimal("0"):
            raise ValueError("Conversion factor must be strictly positive")
        return (base_qty / factor).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)

    @staticmethod
    def validate_whole_units(quantity: Decimal, is_fractional_allowed: bool) -> bool:
        """
        Validates whether a unit can be split.
        If is_fractional_allowed is False (e.g. Tablets, Ampoules), quantity must be an integer.
        Returns True if valid, False if fraction provided for indivisible unit.
        """
        qty = Decimal(str(quantity))
        if not is_fractional_allowed:
            # Check if there is any fractional part
            if qty % Decimal("1") != Decimal("0"):
                return False
        return True

    @staticmethod
    def calculate_allocated_cost(
        paid_quantity: Decimal,
        free_quantity: Decimal,
        unit_purchase_rate: Decimal,
        conversion_factor: Decimal = Decimal("1.0000"),
        discount_percent: Decimal = Decimal("0.00"),
    ) -> Dict[str, Any]:
        """
        Calculates the allocated acquisition cost per base unit when free items are provided.
        The trade discount is deducted from the paid value before allocation, so the
        cost reflects what the pharmacy actually pays (net of discount, excluding GST).
        Example:
          Buy 10 strips @ Rs 90/strip, get 2 strips free.
          Paid qty = 10, Free qty = 2, Rate = 90.
          Total physical strips received = 12.
          Total invoice value = 10 * 90 = 900.
          Allocated cost per strip = 900 / 12 = Rs 75/strip.
          If 1 strip = 10 tablets (conversion_factor = 10),
          Cost per base unit (tablet) = 75 / 10 = Rs 7.50 / tablet.
        """
        paid = Decimal(str(paid_quantity))
        free = Decimal(str(free_quantity))
        rate = Decimal(str(unit_purchase_rate))
        factor = Decimal(str(conversion_factor))
        disc_pct = Decimal(str(discount_percent))

        if paid < Decimal("0") or free < Decimal("0") or rate < Decimal("0"):
            raise ValueError("Quantities and rates must be non-negative")
        if disc_pct < Decimal("0") or disc_pct > Decimal("100"):
            raise ValueError("Discount percent must be between 0 and 100")

        total_received_pack = paid + free
        if total_received_pack == Decimal("0"):
            return {
                "paid_quantity": Decimal("0.00"),
                "free_quantity": Decimal("0.00"),
                "total_quantity_received": Decimal("0.00"),
                "total_cost": Decimal("0.00"),
                "effective_cost_per_pack": Decimal("0.0000"),
                "total_base_units_received": Decimal("0.00"),
                "cost_per_base_unit": Decimal("0.0000"),
            }

        gross_cost = (paid * rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        discount = (gross_cost * (disc_pct / Decimal("100"))).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        total_cost = gross_cost - discount
        effective_cost_per_pack = (total_cost / total_received_pack).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)

        total_base_units = (total_received_pack * factor).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
        cost_per_base_unit = (total_cost / total_base_units).quantize(FOUR_PLACES, rounding=ROUND_HALF_UP)

        return {
            "paid_quantity": paid.quantize(TWO_PLACES),
            "free_quantity": free.quantize(TWO_PLACES),
            "total_quantity_received": total_received_pack.quantize(TWO_PLACES),
            "total_cost": total_cost,
            "effective_cost_per_pack": effective_cost_per_pack,
            "total_base_units_received": total_base_units,
            "cost_per_base_unit": cost_per_base_unit,
        }
