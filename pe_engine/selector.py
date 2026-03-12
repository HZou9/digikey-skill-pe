"""Intelligent power electronics component selection."""
import sys
from pathlib import Path

from .datasheet_parser import parse_from_digikey_params
from .fom import FOMCalculator, GateDriverReqs

# Import DigiKey client from sister project
_dk_root = str(Path(__file__).parent.parent.parent / "digikey-skill")
if _dk_root not in sys.path:
    sys.path.insert(0, _dk_root)

from digikey_api.client import DigiKeyClient
from digikey_api.config import Config as DigiKeyConfig


class PowerComponentSelector:
    """Intelligent power electronics component selection with FOM ranking."""

    # Default search templates by component type and topology
    SEARCH_TEMPLATES = {
        "mosfet": {
            "dab": "MOSFET N-CH {voltage}V SiC",
            "llc": "MOSFET N-CH {voltage}V",
            "buck": "MOSFET N-CH {voltage}V",
            "boost": "MOSFET N-CH {voltage}V",
            "full_bridge": "MOSFET N-CH {voltage}V",
        },
        "gate_driver": {
            "dab": "gate driver isolated half-bridge",
            "llc": "gate driver isolated",
            "buck": "gate driver half-bridge bootstrap",
            "boost": "gate driver high-side",
            "full_bridge": "gate driver isolated",
        },
    }

    def __init__(self, digikey_client: DigiKeyClient | None = None):
        self.dk = digikey_client or DigiKeyClient(DigiKeyConfig())
        self.fom = FOMCalculator()

    def select_mosfet(self, specs: dict) -> dict:
        """Select MOSFETs for a power converter.

        Args:
            specs: Converter specifications
                topology: str - 'dab', 'llc', 'buck', 'boost', 'full_bridge'
                vin: float - Input voltage [V]
                vout: float - Output voltage [V]
                power: float - Output power [W]
                fsw: float - Switching frequency [Hz] or tuple (min, max)
                zvs: bool - ZVS operation desired
                budget: float - Max price per device [USD] (optional)
                n_devices: int - Number of MOSFETs needed (default 4 for bridge)

        Returns:
            Dict with ranked candidates, FOMs, and recommendations.
        """
        topology = specs.get("topology", "dab")
        vin = specs.get("vin", 400)
        vout = specs.get("vout", 48)
        power = specs.get("power", 1000)
        fsw = specs.get("fsw", 100e3)
        zvs = specs.get("zvs", True)
        budget = specs.get("budget")
        n_devices = specs.get("n_devices", 4)

        # Determine voltage requirements
        # For DAB: primary side sees Vin, secondary sees Vout/n_turns
        v_primary = vin
        v_secondary = vout
        v_required = max(v_primary, v_secondary)
        v_min_rating = v_required / 0.8  # 80% derating

        # Standard voltage tiers
        voltage_tiers = [60, 80, 100, 150, 200, 250, 400, 600, 650, 900, 1000, 1200, 1700]
        voltage = min((v for v in voltage_tiers if v >= v_min_rating), default=1200)

        # Build search query
        template = self.SEARCH_TEMPLATES["mosfet"].get(topology, "MOSFET N-CH {voltage}V")
        query = template.format(voltage=voltage)

        # Prefer SiC for high voltage + ZVS
        if voltage >= 600 and zvs and "SiC" not in query:
            query += " SiC"

        # Search DigiKey
        results = self.dk.keyword_search(query, limit=20)
        products = results.get("Products", [])

        if not products:
            return {"error": "No products found", "query": query, "candidates": []}

        # Parse parameters and calculate FOMs
        candidates = []
        fsw_val = fsw if isinstance(fsw, (int, float)) else (fsw[0] + fsw[1]) / 2

        for p in products:
            params = parse_from_digikey_params(p.get("Parameters", []))
            if not params.get("Rds_on"):
                continue  # Skip if can't get Rds(on)

            # Budget filter
            price = p.get("UnitPrice", 0)
            if budget and price > budget:
                continue

            cand = {
                "part_number": p.get("ManufacturerPartNumber", ""),
                "manufacturer": p.get("Manufacturer", ""),
                "digikey_pn": p.get("DigiKeyPartNumber", ""),
                "price": price,
                "stock": p.get("QuantityAvailable", 0),
                "datasheet": p.get("DatasheetUrl", ""),
                "description": p.get("Description", ""),
                "params": params,
            }
            candidates.append(cand)

        if not candidates:
            return {"error": "No candidates with parseable Rds(on)", "query": query, "candidates": []}

        # Estimate current for loss calculation
        i_rms = power / (v_primary * 0.9)  # rough estimate
        op = {
            "i_rms": i_rms,
            "vgs": 18 if voltage >= 600 else 10,
            "fsw": fsw_val,
            "duty": 0.5,
            "power": power,
        }

        # Rank by FOM
        ranked = self.fom.rank_candidates(candidates, topology, op)

        # Add voltage derating check
        for r in ranked:
            vds = r["params"].get("Vds_max")
            if vds:
                r["voltage_derating"] = self.fom.voltage_derating(vds, v_required)

        # Generate recommendation
        recommendation = self._generate_recommendation(ranked, specs, n_devices)

        return {
            "query": query,
            "specs": specs,
            "candidates": ranked,
            "recommendation": recommendation,
            "mock": results.get("_mock", False),
        }

    def select_gate_driver(self, mosfet_params: dict, topology: str = "dab",
                           fsw: float = 100e3) -> dict:
        """Select compatible gate drivers for a chosen MOSFET.

        Args:
            mosfet_params: MOSFET parameters dict (Qg, Ciss, technology, etc.)
            topology: Converter topology
            fsw: Switching frequency [Hz]

        Returns:
            Dict with ranked gate driver candidates.
        """
        reqs = self.fom.gate_driver_requirements(mosfet_params, fsw)

        # Build search query
        template = self.SEARCH_TEMPLATES["gate_driver"].get(topology, "gate driver isolated")
        query = template

        # For DAB: always need isolated drivers
        if topology in ("dab", "llc", "full_bridge"):
            if "isolated" not in query:
                query += " isolated"

        results = self.dk.keyword_search(query, limit=10)
        products = results.get("Products", [])

        candidates = []
        for p in products:
            params = parse_from_digikey_params(p.get("Parameters", []))
            score = self._score_gate_driver(params, reqs)

            candidates.append({
                "part_number": p.get("ManufacturerPartNumber", ""),
                "manufacturer": p.get("Manufacturer", ""),
                "price": p.get("UnitPrice", 0),
                "stock": p.get("QuantityAvailable", 0),
                "datasheet": p.get("DatasheetUrl", ""),
                "params": params,
                "compatibility_score": score,
                "requirements_met": score >= 0.6,
            })

        candidates.sort(key=lambda c: -c["compatibility_score"])

        return {
            "mosfet_requirements": {
                "min_peak_current_A": reqs.min_peak_current_A,
                "recommended_current_A": reqs.recommended_current_A,
                "dead_time_range_ns": (reqs.min_dead_time_ns, reqs.max_dead_time_ns),
                "gate_power_W": reqs.gate_power_W,
                "recommended_vcc": reqs.recommended_vcc,
                "notes": reqs.notes,
            },
            "candidates": candidates,
            "mock": results.get("_mock", False),
        }

    def _score_gate_driver(self, driver_params: dict, reqs: GateDriverReqs) -> float:
        """Score a gate driver's compatibility (0-1)."""
        score = 0.5  # base

        io = driver_params.get("Io_source", 0)
        if io >= reqs.recommended_current_A:
            score += 0.2
        elif io >= reqs.min_peak_current_A:
            score += 0.1

        if driver_params.get("isolation_voltage", 0) > 3000:
            score += 0.15

        cmti = driver_params.get("CMTI", 0)
        if cmti >= 100:
            score += 0.1

        tpd = driver_params.get("tpd", 100)
        if tpd < 25:
            score += 0.1
        elif tpd < 50:
            score += 0.05

        return min(1.0, score)

    def _generate_recommendation(self, ranked: list, specs: dict,
                                 n_devices: int) -> dict:
        """Generate a human-readable recommendation."""
        if not ranked:
            return {"text": "No suitable candidates found."}

        top = ranked[0]
        pn = top["part_number"]
        mfr = top["manufacturer"]
        price = top.get("price", 0)
        total_cost = price * n_devices

        lines = [
            f"Recommended: {pn} ({mfr})",
            f"  Rds(on): {top['params'].get('Rds_on', '?')} mΩ",
        ]

        if top["foms"].rds_qg is not None:
            lines.append(f"  FOM Rds×Qg: {top['foms'].rds_qg:.0f} mΩ·nC")
        if top["foms"].rds_qoss is not None:
            lines.append(f"  FOM Rds×Qoss: {top['foms'].rds_qoss:.0f} mΩ·nC")

        if top.get("losses"):
            l = top["losses"]
            lines.append(f"  Est. losses: {l.P_total:.1f}W (cond={l.P_cond:.1f}, sw={l.P_sw:.1f}, gate={l.P_gate:.2f})")

        lines.append(f"  Price: ${price:.2f}/pc × {n_devices} = ${total_cost:.2f}")

        if len(ranked) > 1:
            alt = ranked[1]
            lines.append(f"  Alternative: {alt['part_number']} ({alt['manufacturer']}) @ ${alt.get('price', 0):.2f}")

        return {
            "text": "\n".join(lines),
            "top_pick": pn,
            "total_bom_cost": total_cost,
        }
