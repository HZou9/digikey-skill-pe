"""BOM optimization: pricing, availability, second-sourcing, CSV export."""
import csv
import io
import sys
from pathlib import Path

_dk_root = str(Path(__file__).parent.parent.parent / "digikey-skill")
if _dk_root not in sys.path:
    sys.path.insert(0, _dk_root)

from digikey_api.client import DigiKeyClient
from digikey_api.config import Config as DigiKeyConfig


# Stock thresholds for inventory alerts
STOCK_THRESHOLDS = {
    "critical": 0,       # out of stock
    "low": 50,           # fewer than 50 pcs
    "warning": 500,      # fewer than 500 pcs
}


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

    def check_stock(self, part_number: str, required_qty: int = 0) -> dict:
        """Check stock level and return alert status.

        Args:
            part_number: Manufacturer or DigiKey part number
            required_qty: Required quantity for production

        Returns:
            Dict with stock level, status, and alert.
        """
        details = self.dk.product_details(part_number)
        product = details.get("Product", {})
        stock = product.get("QuantityAvailable", 0)

        if stock == 0:
            status = "critical"
            alert = "OUT OF STOCK"
        elif stock < STOCK_THRESHOLDS["low"]:
            status = "low"
            alert = f"LOW STOCK ({stock} pcs)"
        elif stock < STOCK_THRESHOLDS["warning"]:
            status = "warning"
            alert = f"Limited ({stock} pcs)"
        else:
            status = "ok"
            alert = None

        # Check if stock covers required quantity
        covers_demand = stock >= required_qty if required_qty > 0 else True

        return {
            "part_number": part_number,
            "stock": stock,
            "status": status,
            "alert": alert,
            "covers_demand": covers_demand,
            "shortage": max(0, required_qty - stock) if required_qty > 0 else 0,
        }

    def optimize_bom(self, components: list, quantity: int = 100,
                     check_inventory: bool = True) -> dict:
        """Optimize a BOM for cost.

        Args:
            components: List of dicts with part_number, quantity_per_unit, etc.
            quantity: Production quantity (units)
            check_inventory: Whether to check stock levels

        Returns:
            Dict with optimized BOM, total cost, and suggestions.
        """
        total_cost = 0
        optimized = []
        stock_alerts = []

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

            entry = {
                "part_number": comp.get("part_number", pn),
                "description": comp.get("description", ""),
                "digikey_pn": pn,
                "qty_per_unit": qty_per_unit,
                "total_qty": total_qty,
                "unit_price": unit_price,
                "line_cost": line_cost,
                "sweet_spot": pricing.get("sweet_spot_qty"),
            }

            # Check stock if requested
            if check_inventory:
                stock_info = self.check_stock(pn, total_qty)
                entry["stock"] = stock_info["stock"]
                entry["stock_status"] = stock_info["status"]
                entry["stock_alert"] = stock_info["alert"]
                entry["covers_demand"] = stock_info["covers_demand"]
                if stock_info["alert"]:
                    stock_alerts.append({
                        "part_number": comp.get("part_number", pn),
                        "alert": stock_info["alert"],
                        "shortage": stock_info["shortage"],
                    })

            optimized.append(entry)

        return {
            "quantity": quantity,
            "components": optimized,
            "total_bom_cost": total_cost,
            "cost_per_unit": total_cost / quantity if quantity > 0 else 0,
            "stock_alerts": stock_alerts,
        }


def bom_to_csv(bom_result: dict, output_path: str | None = None) -> str:
    """Export BOM optimization result to CSV.

    Args:
        bom_result: Result from BOMOptimizer.optimize_bom()
        output_path: Path to save CSV (if None, returns string)

    Returns:
        CSV string or path to saved file.
    """
    components = bom_result.get("components", [])
    if not components:
        return ""

    buf = io.StringIO()
    fieldnames = [
        "part_number", "description", "digikey_pn",
        "qty_per_unit", "total_qty", "unit_price", "line_cost",
        "stock", "stock_status", "sweet_spot",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for comp in components:
        writer.writerow(comp)

    # Summary row
    writer.writerow({
        "part_number": "TOTAL",
        "line_cost": bom_result.get("total_bom_cost", 0),
        "total_qty": f"Qty: {bom_result.get('quantity', 0)} units",
    })

    csv_str = buf.getvalue()

    if output_path:
        Path(output_path).write_text(csv_str)
        return output_path

    return csv_str


def mosfet_selection_to_csv(result: dict, output_path: str | None = None) -> str:
    """Export MOSFET selection result to CSV.

    Args:
        result: Result from PowerComponentSelector.select_mosfet()
        output_path: Path to save CSV (if None, returns string)

    Returns:
        CSV string or path to saved file.
    """
    candidates = result.get("candidates", [])
    if not candidates:
        return ""

    buf = io.StringIO()
    fieldnames = [
        "rank", "part_number", "manufacturer", "price",
        "Rds_on_mOhm", "Qg_nC", "Vds_max_V",
        "FOM_RdsQg", "FOM_RdsQoss", "FOM_RdsCoss",
        "composite_score", "P_cond_W", "P_sw_W", "P_total_W",
        "stock",
    ]
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()

    for i, c in enumerate(candidates, 1):
        p = c.get("params", {})
        f = c.get("foms")
        l = c.get("losses")
        row = {
            "rank": i,
            "part_number": c.get("part_number", ""),
            "manufacturer": c.get("manufacturer", ""),
            "price": c.get("price", 0),
            "Rds_on_mOhm": p.get("Rds_on", ""),
            "Qg_nC": p.get("Qg", ""),
            "Vds_max_V": p.get("Vds_max", ""),
            "FOM_RdsQg": f.rds_qg if f else "",
            "FOM_RdsQoss": f.rds_qoss if f else "",
            "FOM_RdsCoss": f.rds_coss if f else "",
            "composite_score": c.get("composite_score", ""),
            "P_cond_W": round(l.P_cond, 2) if l else "",
            "P_sw_W": round(l.P_sw, 2) if l else "",
            "P_total_W": round(l.P_total, 2) if l else "",
            "stock": c.get("stock", ""),
        }
        writer.writerow(row)

    csv_str = buf.getvalue()

    if output_path:
        Path(output_path).write_text(csv_str)
        return output_path

    return csv_str
