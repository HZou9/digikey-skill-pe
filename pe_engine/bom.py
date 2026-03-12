"""BOM optimization: pricing, availability, second-sourcing."""
import sys
from pathlib import Path

_dk_root = str(Path(__file__).parent.parent.parent / "digikey-skill")
if _dk_root not in sys.path:
    sys.path.insert(0, _dk_root)

from digikey_api.client import DigiKeyClient
from digikey_api.config import Config as DigiKeyConfig


class BOMOptimizer:
    """Optimize BOM for cost, availability, and second-sourcing."""

    # Common footprint-compatible groups
    COMPATIBLE_PACKAGES = {
        "TO-247-3": ["TO-247-3", "TO-247-3L", "TO-247AC"],
        "D2PAK": ["D²Pak (TO-263-3)", "TO-263-3", "D2PAK"],
        "SOIC-8": ["8-SOIC", "SO-8", "SOIC-8"],
        "SOIC-16": ["16-SOIC", "SO-16", "SOIC-16"],
    }

    def __init__(self, digikey_client: DigiKeyClient | None = None):
        self.dk = digikey_client or DigiKeyClient(DigiKeyConfig())

    def pricing_analysis(self, part_number: str,
                         quantities: list | None = None) -> dict:
        """Analyze pricing at different quantities.

        Args:
            part_number: DigiKey part number
            quantities: List of quantities to check (default: standard breaks)

        Returns:
            Dict with pricing tiers and sweet spot analysis.
        """
        if quantities is None:
            quantities = [1, 10, 25, 50, 100, 250, 500, 1000]

        pricing = self.dk.get_pricing(part_number)
        tiers = pricing.get("PricingTiers", [])

        if not tiers:
            return {"error": "No pricing data", "tiers": []}

        # Find sweet spot (biggest price drop per unit increase)
        best_value_qty = tiers[0]["BreakQuantity"]
        best_drop = 0
        for i in range(1, len(tiers)):
            prev = tiers[i - 1]
            curr = tiers[i]
            pct_drop = (prev["UnitPrice"] - curr["UnitPrice"]) / prev["UnitPrice"] * 100
            if pct_drop > best_drop:
                best_drop = pct_drop
                best_value_qty = curr["BreakQuantity"]

        return {
            "part_number": part_number,
            "tiers": tiers,
            "sweet_spot_qty": best_value_qty,
            "sweet_spot_savings_pct": best_drop,
            "unit_price_1pc": tiers[0]["UnitPrice"] if tiers else None,
            "unit_price_100pc": next(
                (t["UnitPrice"] for t in tiers if t["BreakQuantity"] >= 100), None
            ),
        }

    def find_second_sources(self, primary_part: dict) -> list:
        """Find pin/footprint-compatible alternatives.

        Args:
            primary_part: Dict with part_number, package, params

        Returns:
            List of alternative parts with compatibility notes.
        """
        # Use DigiKey substitutions API
        pn = primary_part.get("digikey_pn") or primary_part.get("part_number", "")
        subs_result = self.dk.search_substitutions(pn)
        subs = subs_result.get("Substitutions", [])

        # Also search for similar specs
        params = primary_part.get("params", {})
        vds = params.get("Vds_max")
        if vds:
            search_result = self.dk.keyword_search(
                f"MOSFET N-CH {int(vds)}V",
                limit=5,
            )
            for p in search_result.get("Products", []):
                if p.get("ManufacturerPartNumber") != primary_part.get("part_number"):
                    subs.append({
                        "ManufacturerPartNumber": p["ManufacturerPartNumber"],
                        "Manufacturer": p.get("Manufacturer", ""),
                        "Description": p.get("Description", ""),
                        "source": "parametric_search",
                    })

        return subs

    def optimize_bom(self, components: list, quantity: int = 100) -> dict:
        """Optimize a BOM for cost.

        Args:
            components: List of dicts with part_number, quantity_per_unit, etc.
            quantity: Production quantity (units)

        Returns:
            Dict with optimized BOM, total cost, and suggestions.
        """
        total_cost = 0
        optimized = []

        for comp in components:
            pn = comp.get("digikey_pn") or comp.get("part_number", "")
            qty_per_unit = comp.get("quantity_per_unit", 1)
            total_qty = qty_per_unit * quantity

            pricing = self.pricing_analysis(pn)
            tiers = pricing.get("tiers", [])

            # Find best price for our quantity
            unit_price = None
            for tier in tiers:
                if total_qty >= tier["BreakQuantity"]:
                    unit_price = tier["UnitPrice"]

            if unit_price is None and tiers:
                unit_price = tiers[0]["UnitPrice"]

            line_cost = (unit_price or 0) * total_qty
            total_cost += line_cost

            optimized.append({
                "part_number": comp.get("part_number", pn),
                "digikey_pn": pn,
                "qty_per_unit": qty_per_unit,
                "total_qty": total_qty,
                "unit_price": unit_price,
                "line_cost": line_cost,
                "sweet_spot": pricing.get("sweet_spot_qty"),
            })

        return {
            "quantity": quantity,
            "components": optimized,
            "total_bom_cost": total_cost,
            "cost_per_unit": total_cost / quantity if quantity > 0 else 0,
        }
