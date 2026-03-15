"""Intelligent power electronics component selection."""
import re
import sys
from pathlib import Path

from .datasheet_parser import parse_from_digikey_params
from .fom import FOMCalculator, GateDriverReqs

# Ensure repo root is on path for digikey_api import
_repo_root = str(Path(__file__).parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

from digikey_api.client import DigiKeyClient
from digikey_api.config import Config as DigiKeyConfig


class PowerComponentSelector:
    """Intelligent power electronics component selection with FOM ranking."""

    # Default search templates by component type and topology
    SEARCH_TEMPLATES = {
        "mosfet": {
            "dab": "MOSFET N-CH {voltage}V SiC",
            "llc": "MOSFET N-CH {voltage}V",
            "cllc": "MOSFET N-CH {voltage}V SiC",
            "buck": "MOSFET N-CH {voltage}V",
            "boost": "MOSFET N-CH {voltage}V",
            "full_bridge": "MOSFET N-CH {voltage}V",
            "npc": "MOSFET N-CH {voltage}V",
            "t_type": "MOSFET N-CH {voltage}V",
            "pfc": "MOSFET N-CH {voltage}V SiC",
        },
        "gate_driver": {
            "dab": "gate driver isolated half-bridge",
            "llc": "gate driver isolated",
            "cllc": "gate driver isolated half-bridge",
            "buck": "gate driver half-bridge bootstrap",
            "boost": "gate driver high-side",
            "full_bridge": "gate driver isolated",
            "npc": "gate driver isolated half-bridge",
            "t_type": "gate driver isolated",
            "pfc": "gate driver high-side",
        },
        "power_module": {
            "dab": "SiC module half bridge {voltage}V",
            "llc": "IGBT module half bridge {voltage}V",
            "cllc": "SiC module half bridge {voltage}V",
            "buck": "power module half bridge {voltage}V",
            "boost": "power module boost {voltage}V",
            "full_bridge": "IGBT module {voltage}V",
            "inverter": "power module sixpack {voltage}V",
            "npc": "IGBT module half bridge {voltage}V",
            "t_type": "IGBT module half bridge {voltage}V",
            "pfc": "power module boost {voltage}V",
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

        # Build search queries — multiple queries for broader coverage
        template = self.SEARCH_TEMPLATES["mosfet"].get(topology, "MOSFET N-CH {voltage}V")
        base_query = template.format(voltage=voltage)

        # Prefer SiC for high voltage + ZVS
        if voltage >= 600 and zvs and "SiC" not in base_query:
            base_query += " SiC"

        # Multi-query strategy: cover all major manufacturers and nearby voltage tiers
        queries = [base_query]
        if voltage >= 600:
            # Also search the next voltage tier (e.g., 600→650)
            idx = voltage_tiers.index(voltage) if voltage in voltage_tiers else -1
            nearby_voltages = [voltage]
            if idx >= 0 and idx + 1 < len(voltage_tiers):
                nearby_voltages.append(voltage_tiers[idx + 1])

            for v in nearby_voltages:
                for mfr_query in [
                    f"SiC MOSFET {v}V",
                    f"CoolSiC {v}V MOSFET",
                ]:
                    if mfr_query not in queries:
                        queries.append(mfr_query)

        # Search DigiKey with all queries and merge results
        seen_base_pns = set()
        products = []
        is_mock = False
        for q in queries:
            results = self.dk.keyword_search(q, limit=20)
            is_mock = is_mock or results.get("_mock", False)
            for p in results.get("Products", []):
                pn = p.get("ManufacturerPartNumber", "")
                # Deduplicate packaging variants (-TR, -CT, -DKR suffixes)
                base_pn = re.sub(r'[-]?(TR|CT|DKR|TND|XTMA1)$', '', pn)
                if base_pn not in seen_base_pns:
                    seen_base_pns.add(base_pn)
                    products.append(p)

        if not products:
            return {"error": "No products found", "query": queries[0], "candidates": []}

        # Parse parameters and calculate FOMs
        candidates = []
        fsw_val = fsw if isinstance(fsw, (int, float)) else (fsw[0] + fsw[1]) / 2

        for p in products:
            params = parse_from_digikey_params(p.get("Parameters", []))
            if not params.get("Rds_on"):
                continue  # Skip if can't get Rds(on)

            # Voltage class filter: skip devices with Vds > 2x required voltage
            vds = params.get("Vds_max")
            if vds and vds > v_required * 2:
                continue

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
            return {"error": "No candidates with parseable Rds(on)", "query": queries[0], "candidates": []}

        # Rank by FOM (loss calculation left to user for their specific application)
        ranked = self.fom.rank_candidates(candidates, topology)

        # Add voltage derating check
        for r in ranked:
            vds = r["params"].get("Vds_max")
            if vds:
                r["voltage_derating"] = self.fom.voltage_derating(vds, v_required)

        # Generate recommendation
        recommendation = self._generate_recommendation(ranked, specs, n_devices)

        return {
            "query": queries[0],
            "queries_used": queries,
            "specs": specs,
            "candidates": ranked,
            "recommendation": recommendation,
            "mock": is_mock,
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

        # Build search queries
        template = self.SEARCH_TEMPLATES["gate_driver"].get(topology, "gate driver isolated")
        query = template

        # For DAB: always need isolated drivers
        if topology in ("dab", "llc", "full_bridge"):
            if "isolated" not in query:
                query += " isolated"

        # Multi-query: parametric searches at different current levels
        # to naturally cover different vendors and price points
        io_a = int(reqs.recommended_current_A)
        is_isolated = "isolated" in query or topology in ("dab", "llc", "cllc", "full_bridge")
        iso_kw = "isolated" if is_isolated else ""

        queries = [query]
        # Search at the recommended current and nearby tiers
        for io_tier in sorted(set([io_a, io_a + 2, max(4, io_a - 2), 10])):
            q = f"gate driver {iso_kw} {io_tier}A".strip()
            if q not in queries:
                queries.append(q)
        # Half-bridge specific query
        if topology in ("dab", "full_bridge", "npc", "t_type"):
            queries.append(f"gate driver half bridge {io_a}A")
        # SiC-specific drivers
        if mosfet_params.get("technology") == "SiC":
            queries.append(f"gate driver SiC {iso_kw}".strip())

        # Search and merge results, dedup packaging variants
        seen_base_pns = set()
        products = []
        is_mock = False
        for q in queries:
            results = self.dk.keyword_search(q, limit=15)
            is_mock = is_mock or results.get("_mock", False)
            for p in results.get("Products", []):
                pn = p.get("ManufacturerPartNumber", "")
                desc = p.get("Description", "").lower()
                # Skip eval boards, modules, dev kits
                if any(skip in desc for skip in ["eval", "development", "demo", "module", "board"]):
                    continue
                # Deduplicate packaging variants
                base_pn = re.sub(r'[-]?(TR|CT|DKR|TND|RL)$', '', pn)
                if base_pn not in seen_base_pns:
                    seen_base_pns.add(base_pn)
                    products.append(p)

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

        # Sort by score, then diversify: interleave vendors so no single
        # vendor dominates the top of the list
        candidates.sort(key=lambda c: -c["compatibility_score"])
        diversified = self._diversify_by_vendor(candidates)

        return {
            "mosfet_requirements": {
                "min_peak_current_A": reqs.min_peak_current_A,
                "recommended_current_A": reqs.recommended_current_A,
                "dead_time_range_ns": (reqs.min_dead_time_ns, reqs.max_dead_time_ns),
                "gate_power_W": reqs.gate_power_W,
                "recommended_vcc": reqs.recommended_vcc,
                "notes": reqs.notes,
            },
            "candidates": diversified,
            "mock": is_mock,
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

    @staticmethod
    def _diversify_by_vendor(candidates: list, max_per_vendor: int = 5) -> list:
        """Reorder candidates to interleave vendors for balanced comparison.

        Takes top max_per_vendor from each vendor, then round-robin interleaves
        them so users see options from different manufacturers.
        """
        from collections import defaultdict
        by_vendor = defaultdict(list)
        for c in candidates:
            mfr = c.get("manufacturer", "Unknown")
            if len(by_vendor[mfr]) < max_per_vendor:
                by_vendor[mfr].append(c)

        # Round-robin interleave: take one from each vendor in turn
        result = []
        vendor_iters = {mfr: iter(parts) for mfr, parts in by_vendor.items()}
        while vendor_iters:
            exhausted = []
            for mfr, it in vendor_iters.items():
                try:
                    result.append(next(it))
                except StopIteration:
                    exhausted.append(mfr)
            for mfr in exhausted:
                del vendor_iters[mfr]

        return result

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

        lines.append(f"  Qg: {top['params'].get('Qg', '?')} nC")
        lines.append(f"  Price: ${price:.2f}/pc × {n_devices} = ${total_cost:.2f}")

        if len(ranked) > 1:
            alt = ranked[1]
            lines.append(f"  Alternative: {alt['part_number']} ({alt['manufacturer']}) @ ${alt.get('price', 0):.2f}")

        return {
            "text": "\n".join(lines),
            "top_pick": pn,
            "total_bom_cost": total_cost,
        }

    # --- Power Module Selection ---

    def select_power_module(self, specs: dict) -> dict:
        """Select power modules for high-power converters.

        Args:
            specs: Converter specifications
                topology: str - 'dab', 'llc', 'full_bridge', 'inverter'
                vin: float - Input voltage [V]
                vout: float - Output voltage [V]
                power: float - Output power [W]
                fsw: float - Switching frequency [Hz]
                cooling: str - 'air' or 'liquid' (default: 'air')
                budget: float - Max price per module [USD] (optional)
                prefer_sic: bool - Prefer SiC over IGBT (default: auto)

        Returns:
            Dict with ranked module candidates and thermal analysis.
        """
        topology = specs.get("topology", "dab")
        vin = specs.get("vin", 800)
        vout = specs.get("vout", 400)
        power = specs.get("power", 100000)
        fsw = specs.get("fsw", 20e3)
        cooling = specs.get("cooling", "air")
        budget = specs.get("budget")
        prefer_sic = specs.get("prefer_sic")

        v_required = max(vin, vout)
        v_min_rating = v_required / 0.8

        voltage_tiers = [600, 650, 900, 1200, 1700, 3300]
        voltage = min((v for v in voltage_tiers if v >= v_min_rating), default=1700)

        # Auto-decide SiC vs IGBT
        if prefer_sic is None:
            prefer_sic = fsw > 30e3 or voltage >= 1200

        template = self.SEARCH_TEMPLATES["power_module"].get(
            topology, "power module half bridge {voltage}V")
        query = template.format(voltage=voltage)

        if prefer_sic and "sic" not in query.lower():
            query = query.replace("IGBT", "SiC").replace("igbt", "SiC")
            if "SiC" not in query:
                query += " SiC"

        results = self.dk.keyword_search(query, limit=10)
        products = results.get("Products", [])

        if not products:
            return {"error": "No power modules found", "query": query, "candidates": []}

        # Estimate required module current
        i_rms = power / (v_required * 0.9)
        t_ambient = 40.0 if cooling == "air" else 30.0

        candidates = []
        for p in products:
            params = self._parse_module_params(p.get("Parameters", []))

            if budget and p.get("UnitPrice", 0) > budget:
                continue

            ic = params.get("Ic_25C", 0)
            if ic < i_rms * 0.5:
                continue

            # Thermal estimate
            rth_jc = params.get("RthJC", 0.05)
            pd_max = params.get("Pd_max", 2000)
            p_loss_est = power * 0.02  # rough 2% loss estimate

            if cooling == "liquid":
                rth_total = rth_jc + 0.05  # cold plate ~ 0.05°C/W
            else:
                rth_total = rth_jc + 0.3  # forced air heatsink ~ 0.3°C/W

            tj_est = t_ambient + p_loss_est * rth_total
            tj_max = 175 if params.get("technology") == "SiC" else 150

            cand = {
                "part_number": p.get("ManufacturerPartNumber", ""),
                "manufacturer": p.get("Manufacturer", ""),
                "digikey_pn": p.get("DigiKeyPartNumber", ""),
                "price": p.get("UnitPrice", 0),
                "stock": p.get("QuantityAvailable", 0),
                "datasheet": p.get("DatasheetUrl", ""),
                "description": p.get("Description", ""),
                "params": params,
                "thermal": {
                    "Tj_estimated_C": round(tj_est, 1),
                    "Tj_max_C": tj_max,
                    "thermal_ok": tj_est < tj_max * 0.9,
                    "P_loss_estimated_W": round(p_loss_est, 1),
                    "cooling": cooling,
                },
                "current_margin_pct": round((ic / i_rms - 1) * 100, 0) if i_rms > 0 else 0,
                "voltage_derating": self.fom.voltage_derating(
                    params.get("Vces", voltage), v_required),
            }
            candidates.append(cand)

        # Sort: prefer adequate current, good thermal margin, lower price
        candidates.sort(key=lambda c: (
            not c["thermal"]["thermal_ok"],
            -c["current_margin_pct"],
            c["price"],
        ))

        recommendation = self._generate_module_recommendation(
            candidates, specs, power)

        return {
            "query": query,
            "specs": specs,
            "candidates": candidates,
            "recommendation": recommendation,
            "mock": results.get("_mock", False),
        }

    def _parse_module_params(self, parameters: list) -> dict:
        """Parse power module parameters from DigiKey API response."""
        specs = {}
        for p in parameters:
            name = p.get("Name", "")
            value = p.get("Value", "")

            if "Voltage - Collector Emitter" in name or "Vces" in name:
                m = re.search(r"(\d+\.?\d*)\s*V", value)
                if m:
                    specs["Vces"] = float(m.group(1))

            elif "Current - Collector" in name and "25°C" in name:
                m = re.search(r"(\d+\.?\d*)\s*A", value)
                if m:
                    specs["Ic_25C"] = float(m.group(1))

            elif "Current - Collector" in name and "80°C" in name:
                m = re.search(r"(\d+\.?\d*)\s*A", value)
                if m:
                    specs["Ic_80C"] = float(m.group(1))

            elif "Rds On" in name:
                m = re.search(r"(\d+\.?\d*)\s*(m?Ω|mohm)", value, re.IGNORECASE)
                if m:
                    v = float(m.group(1))
                    specs["Rds_on"] = v if "m" in m.group(2).lower() else v * 1000

            elif "Vce(sat)" in name:
                m = re.search(r"(\d+\.?\d*)\s*V", value)
                if m:
                    specs["Vce_sat"] = float(m.group(1))

            elif "Power Dissipation" in name:
                m = re.search(r"(\d+\.?\d*)\s*W", value)
                if m:
                    specs["Pd_max"] = float(m.group(1))

            elif "Thermal Resistance Junction-Case" in name:
                m = re.search(r"(\d+\.?\d*)\s*°C/W", value)
                if m:
                    specs["RthJC"] = float(m.group(1))

            elif "Eon + Eoff" in name:
                m = re.search(r"(\d+\.?\d*)\s*(m?J)", value)
                if m:
                    v = float(m.group(1))
                    specs["Esw"] = v if "m" in m.group(2) else v * 1000

            elif "Module Type" in name:
                specs["module_type"] = value

            elif "Technology" in name:
                specs["technology"] = value
                if "sic" in value.lower():
                    specs["is_sic"] = True

            elif "Package" in name or "Case" in name:
                specs["package"] = value

        return specs

    def _generate_module_recommendation(self, ranked: list, specs: dict,
                                        power: float) -> dict:
        """Generate recommendation for power module selection."""
        if not ranked:
            return {"text": "No suitable power modules found."}

        top = ranked[0]
        pn = top["part_number"]
        mfr = top["manufacturer"]
        price = top.get("price", 0)
        tech = top["params"].get("technology", "")
        ic = top["params"].get("Ic_25C", "?")
        vces = top["params"].get("Vces", "?")

        lines = [
            f"Recommended: {pn} ({mfr})",
            f"  Technology: {tech}",
            f"  Rating: {vces}V / {ic}A",
            f"  Thermal: Tj={top['thermal']['Tj_estimated_C']}°C "
            f"({'OK' if top['thermal']['thermal_ok'] else 'WARNING'})",
            f"  Price: ${price:.2f}",
        ]

        if power > 200000:
            lines.append("  Note: >200kW — consider paralleling modules or custom solution")

        if len(ranked) > 1:
            alt = ranked[1]
            lines.append(f"  Alternative: {alt['part_number']} ({alt['manufacturer']}) "
                         f"@ ${alt.get('price', 0):.2f}")

        return {"text": "\n".join(lines), "top_pick": pn}

    # --- Heatsink Selection ---

    def select_heatsink(self, specs: dict) -> dict:
        """Select heatsink for power devices.

        Args:
            specs: Thermal requirements
                p_loss: float - Total power dissipation [W]
                rth_jc: float - Junction-case thermal resistance [°C/W]
                rth_cs: float - Case-sink thermal resistance [°C/W] (default 0.1)
                tj_max: float - Max junction temperature [°C] (default 175)
                t_ambient: float - Ambient temperature [°C] (default 40)
                cooling: str - 'natural', 'forced_air', 'liquid'
                max_size_mm: tuple - (length, width, height) max dimensions

        Returns:
            Dict with heatsink candidates and thermal analysis.
        """
        p_loss = specs.get("p_loss", 100)
        rth_jc = specs.get("rth_jc", 0.5)
        rth_cs = specs.get("rth_cs", 0.1)  # thermal grease/pad
        tj_max = specs.get("tj_max", 175)
        t_ambient = specs.get("t_ambient", 40)
        cooling = specs.get("cooling", "forced_air")

        # Calculate required heatsink thermal resistance
        rth_sa_required = (tj_max - t_ambient) / p_loss - rth_jc - rth_cs
        if rth_sa_required <= 0:
            return {
                "error": f"Cannot cool {p_loss}W with air: need liquid cooling or lower power",
                "rth_sa_required": rth_sa_required,
                "specs": specs,
                "candidates": [],
            }

        # Search DigiKey
        if cooling == "liquid":
            query = "cold plate liquid cooling"
        else:
            query = "heatsink aluminum"

        results = self.dk.keyword_search(query, limit=10)
        products = results.get("Products", [])

        candidates = []
        for p in products:
            params = self._parse_heatsink_params(p.get("Parameters", []))

            # Get appropriate thermal resistance
            if cooling == "natural":
                rth = params.get("Rth_natural", params.get("Rth_forced_200", 999))
            elif cooling == "liquid":
                rth = params.get("Rth_liquid", params.get("Rth_forced_400", 999))
            else:
                rth = params.get("Rth_forced_200", params.get("Rth_natural", 999))

            if rth >= 999:
                continue

            tj_est = t_ambient + p_loss * (rth + rth_cs + rth_jc)
            thermal_ok = tj_est < tj_max

            cand = {
                "part_number": p.get("ManufacturerPartNumber", ""),
                "manufacturer": p.get("Manufacturer", ""),
                "price": p.get("UnitPrice", 0),
                "stock": p.get("QuantityAvailable", 0),
                "description": p.get("Description", ""),
                "params": params,
                "thermal": {
                    "Rth_SA": rth,
                    "Rth_SA_required": round(rth_sa_required, 3),
                    "adequate": rth <= rth_sa_required,
                    "Tj_estimated_C": round(tj_est, 1),
                    "Tj_max_C": tj_max,
                    "margin_C": round(tj_max - tj_est, 1),
                },
            }
            candidates.append(cand)

        candidates.sort(key=lambda c: (
            not c["thermal"]["adequate"],
            c["thermal"]["Rth_SA"],
            c["price"],
        ))

        return {
            "specs": specs,
            "rth_sa_required": round(rth_sa_required, 3),
            "candidates": candidates,
            "mock": results.get("_mock", False),
        }

    def _parse_heatsink_params(self, parameters: list) -> dict:
        """Parse heatsink parameters from DigiKey API response."""
        specs = {}
        for p in parameters:
            name = p.get("Name", "")
            value = p.get("Value", "")

            if "Natural Convection" in name or "natural" in name.lower():
                m = re.search(r"(\d+\.?\d*)\s*°C/W", value)
                if m:
                    specs["Rth_natural"] = float(m.group(1))

            elif "200 LFM" in name:
                m = re.search(r"(\d+\.?\d*)\s*°C/W", value)
                if m:
                    specs["Rth_forced_200"] = float(m.group(1))

            elif "400 LFM" in name:
                m = re.search(r"(\d+\.?\d*)\s*°C/W", value)
                if m:
                    specs["Rth_forced_400"] = float(m.group(1))

            elif "1 GPM" in name:
                m = re.search(r"(\d+\.?\d*)\s*°C/W", value)
                if m:
                    specs["Rth_liquid"] = float(m.group(1))

            elif "Length" in name:
                m = re.search(r"(\d+\.?\d*)\s*mm", value)
                if m:
                    specs["length_mm"] = float(m.group(1))

            elif "Width" in name:
                m = re.search(r"(\d+\.?\d*)\s*mm", value)
                if m:
                    specs["width_mm"] = float(m.group(1))

            elif "Height" in name:
                m = re.search(r"(\d+\.?\d*)\s*mm", value)
                if m:
                    specs["height_mm"] = float(m.group(1))

            elif "Type" in name:
                specs["type"] = value

            elif "Material" in name:
                specs["material"] = value

        return specs

    # ================================================================
    # Capacitor Selection
    # ================================================================

    CAPACITOR_QUERIES = {
        "dc_link": [
            "film capacitor {voltage}",
            "aluminum electrolytic capacitor {voltage}",
            "polypropylene capacitor {voltage}",
        ],
        "resonant": [
            "film capacitor {voltage} polypropylene",
            "mica capacitor {voltage}",
        ],
        "filter": [
            "MLCC ceramic capacitor {voltage}",
            "film capacitor {voltage}",
        ],
        "emi_x": [
            "X2 safety capacitor",
            "X1 safety capacitor",
        ],
        "emi_y": [
            "Y2 safety capacitor",
            "Y1 safety capacitor",
        ],
        "snubber": [
            "film capacitor {voltage} polypropylene snubber",
            "ceramic capacitor C0G {voltage}",
        ],
        "bootstrap": [
            "MLCC ceramic capacitor 25V",
            "MLCC ceramic capacitor 50V",
        ],
    }

    def select_capacitor(self, specs: dict) -> dict:
        """Search capacitors for power electronics applications.

        Args:
            specs: Dict with keys:
                cap_type: dc_link|resonant|filter|emi_x|emi_y|snubber|bootstrap
                voltage: Required voltage rating [V]
                capacitance: Target capacitance string (e.g., "10uF", optional)
                budget: Max price (optional)

        Returns:
            Dict with search results and parameters.
        """
        cap_type = specs.get("cap_type", "dc_link")
        voltage = specs.get("voltage", 400)
        capacitance = specs.get("capacitance", "")
        budget = specs.get("budget")

        # Format voltage: use kV for >= 1000V (DigiKey search works better)
        if voltage >= 1000:
            v_str = f"{voltage / 1000:.0f}kV" if voltage % 1000 == 0 else f"{voltage / 1000:.1f}kV"
        else:
            v_str = f"{int(voltage)}V"

        templates = self.CAPACITOR_QUERIES.get(cap_type, self.CAPACITOR_QUERIES["dc_link"])
        queries = [t.format(voltage=v_str) for t in templates]

        # Append capacitance to queries if specified
        if capacitance:
            queries = [f"{q} {capacitance}" for q in queries]

        seen_base_pns = set()
        products = []
        is_mock = False
        for q in queries:
            results = self.dk.keyword_search(q, limit=20)
            is_mock = is_mock or results.get("_mock", False)
            for p in results.get("Products", []):
                pn = p.get("ManufacturerPartNumber", "")
                base_pn = re.sub(r'[-]?(TR|CT|DKR)$', '', pn)
                desc = p.get("Description", "").lower()
                if any(skip in desc for skip in ["eval", "kit", "board"]):
                    continue
                if base_pn not in seen_base_pns:
                    seen_base_pns.add(base_pn)
                    products.append(p)

        candidates = []
        for p in products:
            params = self._parse_capacitor_params(p.get("Parameters", []))

            # Filter: voltage rating must meet or exceed the requirement
            v_rated = params.get("voltage_rated")
            if v_rated is not None and v_rated < voltage:
                continue

            price = p.get("UnitPrice", 0)
            if budget and price > budget:
                continue
            candidates.append({
                "part_number": p.get("ManufacturerPartNumber", ""),
                "manufacturer": p.get("Manufacturer", ""),
                "price": price,
                "stock": p.get("QuantityAvailable", 0),
                "datasheet": p.get("DatasheetUrl", ""),
                "description": p.get("Description", ""),
                "params": params,
            })

        return {
            "cap_type": cap_type,
            "voltage": voltage,
            "candidates": self._diversify_by_vendor(candidates),
            "mock": is_mock,
        }

    @staticmethod
    def _parse_capacitor_params(parameters: list) -> dict:
        specs = {}
        for p in parameters:
            name = p.get("Name", "")
            value = p.get("Value", "")
            if "Capacitance" in name and "Tolerance" not in name:
                specs["capacitance"] = value
            elif "Voltage" in name and ("Rated" in name or "Rating" in name):
                m = re.search(r"(\d+\.?\d*)\s*(V|kV)", value)
                if m:
                    v = float(m.group(1))
                    specs["voltage_rated"] = v * 1000 if m.group(2) == "kV" else v
                specs["voltage_rated_str"] = value
            elif "Temperature Coefficient" in name or "Dielectric" in name:
                specs["dielectric"] = value
            elif "Tolerance" in name:
                specs["tolerance"] = value
            elif "ESR" in name:
                specs["esr"] = value
            elif "Ripple Current" in name:
                specs["ripple_current"] = value
            elif "Lifetime" in name:
                specs["lifetime"] = value
            elif "Package" in name or "Case" in name:
                specs["package"] = value
            elif "Mounting" in name:
                specs["mounting"] = value
        return specs

    # ================================================================
    # Magnetics Selection
    # ================================================================

    MAGNETICS_QUERIES = {
        "ferrite_core": [
            "ferrite core E",
            "ferrite core PQ",
            "ferrite core toroidal",
            "ferrite core RM",
            "ferrite core ETD",
        ],
        "nanocrystalline": [
            "nanocrystalline toroidal core",
            "amorphous core toroidal",
        ],
        "inductor": [
            "power inductor {current}A",
            "inductor shielded {current}A",
        ],
        "pfc_inductor": [
            "PFC inductor",
            "PFC choke",
            "boost inductor {current}A",
        ],
        "transformer": [
            "gate drive transformer",
            "pulse transformer",
            "power transformer",
        ],
        "bobbin": [
            "bobbin E core",
            "bobbin PQ",
            "bobbin ETD",
            "coil former",
        ],
        "cmc": [
            "common mode choke {current}A",
            "common mode filter {current}A",
        ],
        "emi_filter": [
            "EMI filter {current}A",
            "line filter {current}A",
            "AC EMI filter",
        ],
    }

    def select_magnetics(self, specs: dict) -> dict:
        """Search magnetic components for power electronics.

        Args:
            specs: Dict with keys:
                mag_type: ferrite_core|nanocrystalline|inductor|pfc_inductor|
                          transformer|bobbin|cmc|emi_filter
                current: Rated current [A] (for inductors/chokes)
                inductance: Target inductance (optional, for display)
                budget: Max price (optional)

        Returns:
            Dict with search results.
        """
        mag_type = specs.get("mag_type", "ferrite_core")
        current = specs.get("current", 10)
        budget = specs.get("budget")

        templates = self.MAGNETICS_QUERIES.get(mag_type, self.MAGNETICS_QUERIES["ferrite_core"])
        queries = [t.format(current=int(current)) for t in templates]

        seen_base_pns = set()
        products = []
        is_mock = False
        for q in queries:
            results = self.dk.keyword_search(q, limit=20)
            is_mock = is_mock or results.get("_mock", False)
            for p in results.get("Products", []):
                pn = p.get("ManufacturerPartNumber", "")
                base_pn = re.sub(r'[-]?(TR|CT|DKR)$', '', pn)
                desc = p.get("Description", "").lower()
                if any(skip in desc for skip in ["eval", "kit", "demo"]):
                    continue
                if base_pn not in seen_base_pns:
                    seen_base_pns.add(base_pn)
                    products.append(p)

        candidates = []
        for p in products:
            params = self._parse_magnetics_params(p.get("Parameters", []))
            price = p.get("UnitPrice", 0)
            if budget and price > budget:
                continue
            candidates.append({
                "part_number": p.get("ManufacturerPartNumber", ""),
                "manufacturer": p.get("Manufacturer", ""),
                "price": price,
                "stock": p.get("QuantityAvailable", 0),
                "datasheet": p.get("DatasheetUrl", ""),
                "description": p.get("Description", ""),
                "params": params,
            })

        return {
            "mag_type": mag_type,
            "candidates": self._diversify_by_vendor(candidates),
            "mock": is_mock,
        }

    @staticmethod
    def _parse_magnetics_params(parameters: list) -> dict:
        specs = {}
        for p in parameters:
            name = p.get("Name", "")
            value = p.get("Value", "")
            if "Inductance" in name and "Tolerance" not in name:
                specs["inductance"] = value
            elif "Current Rating" in name or "Current - Max" in name:
                specs["current_rating"] = value
            elif "Current - Saturation" in name:
                specs["i_sat"] = value
            elif "DC Resistance" in name or "DCR" in name:
                specs["dcr"] = value
            elif "Material" in name:
                specs["material"] = value
            elif "Core Type" in name or "Type" in name:
                specs["core_type"] = value
            elif "Shielding" in name:
                specs["shielding"] = value
            elif "Size" in name or "Dimensions" in name:
                specs["size"] = value
            elif "Package" in name or "Case" in name:
                specs["package"] = value
            elif "Mounting" in name:
                specs["mounting"] = value
            elif "Frequency" in name:
                specs["frequency"] = value
            elif "Impedance" in name:
                specs["impedance"] = value
            elif "Turns Ratio" in name:
                specs["turns_ratio"] = value
            elif "Voltage - Isolation" in name:
                specs["isolation"] = value
        return specs

    # ================================================================
    # Cross-Reference / Substitution
    # ================================================================

    def find_substitutes(self, part_number: str) -> dict:
        """Find substitute/alternative parts for a given component.

        Uses DigiKey substitutions API + keyword search for alternatives.

        Args:
            part_number: Manufacturer part number to find alternatives for.

        Returns:
            Dict with original part info and substitute candidates.
        """
        # First, get the original part details
        original = None
        search_results = self.dk.keyword_search(part_number, limit=5)
        for p in search_results.get("Products", []):
            if part_number.upper() in p.get("ManufacturerPartNumber", "").upper():
                original = {
                    "part_number": p.get("ManufacturerPartNumber", ""),
                    "manufacturer": p.get("Manufacturer", ""),
                    "description": p.get("Description", ""),
                    "price": p.get("UnitPrice", 0),
                    "stock": p.get("QuantityAvailable", 0),
                    "params": parse_from_digikey_params(p.get("Parameters", [])),
                    "digikey_pn": p.get("DigiKeyPartNumber", ""),
                    "datasheet": p.get("DatasheetUrl", ""),
                }
                break

        if not original:
            return {
                "error": f"Part {part_number} not found on DigiKey",
                "part_number": part_number,
                "substitutes": [],
            }

        # Try DigiKey substitutions API
        substitutes = []
        if original.get("digikey_pn"):
            try:
                subs = self.dk.search_substitutions(original["digikey_pn"])
                for s in subs.get("Substitutions", []):
                    substitutes.append({
                        "part_number": s.get("ManufacturerPartNumber",
                                             s.get("ManufacturerProductNumber", "")),
                        "manufacturer": s.get("Manufacturer", ""),
                        "description": s.get("Description", ""),
                        "source": "DigiKey substitution",
                    })
            except Exception:
                pass

        # Also search by key specs to find functional equivalents
        params = original["params"]
        desc = original.get("description", "")

        # Build a parametric search query from the original part
        equiv_queries = []
        if params.get("Vds_max") and params.get("Rds_on"):
            # MOSFET cross-reference
            equiv_queries.append(
                f"MOSFET {int(params['Vds_max'])}V {int(params['Rds_on'])}mOhm")
        elif params.get("Io_source") and params.get("isolation_voltage"):
            # Gate driver cross-reference
            equiv_queries.append(
                f"gate driver isolated {int(params['Io_source'])}A")
        elif "capacitor" in desc.lower() or "cap" in desc.lower():
            equiv_queries.append(desc[:50])
        else:
            # Generic: use description keywords
            equiv_queries.append(desc[:40])

        seen_pns = {original["part_number"].upper()}
        for s in substitutes:
            seen_pns.add(s.get("part_number", "").upper())

        for q in equiv_queries:
            results = self.dk.keyword_search(q, limit=15)
            for p in results.get("Products", []):
                pn = p.get("ManufacturerPartNumber", "")
                if pn.upper() not in seen_pns:
                    seen_pns.add(pn.upper())
                    sub_params = parse_from_digikey_params(p.get("Parameters", []))
                    substitutes.append({
                        "part_number": pn,
                        "manufacturer": p.get("Manufacturer", ""),
                        "description": p.get("Description", ""),
                        "price": p.get("UnitPrice", 0),
                        "stock": p.get("QuantityAvailable", 0),
                        "params": sub_params,
                        "source": "parametric search",
                    })

        return {
            "original": original,
            "substitutes": self._diversify_by_vendor(substitutes),
            "mock": search_results.get("_mock", False),
        }
