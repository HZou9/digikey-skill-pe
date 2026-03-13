"""Figure of Merit calculations and loss estimation for power electronics."""
import math
from dataclasses import dataclass


@dataclass
class FOMs:
    """FOM calculation results."""
    rds_qg: float | None = None       # mΩ·nC (lower is better)
    rds_qoss: float | None = None     # mΩ·nC (lower for ZVS)
    rds_coss: float | None = None     # mΩ·pF
    rds_qg_per_dollar: float | None = None  # FOM/$ (lower is better value)


@dataclass
class LossEstimate:
    """Power loss estimation results."""
    P_cond: float = 0.0      # Conduction loss [W]
    P_sw: float = 0.0        # Switching loss [W]
    P_gate: float = 0.0      # Gate drive loss [W]
    P_body_diode: float = 0.0  # Body diode loss [W]
    P_total: float = 0.0     # Total loss [W]
    eta_impact: float = 0.0  # Efficiency impact [%]

    def __post_init__(self):
        self.P_total = self.P_cond + self.P_sw + self.P_gate + self.P_body_diode


@dataclass
class GateDriverReqs:
    """Gate driver requirements derived from MOSFET params."""
    min_peak_current_A: float = 0.0
    recommended_current_A: float = 0.0
    min_dead_time_ns: float = 0.0
    max_dead_time_ns: float = 0.0
    gate_power_W: float = 0.0
    recommended_vcc: float = 0.0
    notes: list = None

    def __post_init__(self):
        if self.notes is None:
            self.notes = []


class FOMCalculator:
    """Calculate Figures of Merit and estimate losses for power MOSFETs."""

    # Topology-specific FOM weights: {topology: {fom_name: weight}}
    TOPOLOGY_WEIGHTS = {
        "dab": {"rds_qoss": 0.5, "rds_qg": 0.3, "rds_coss": 0.2},
        "llc": {"rds_qoss": 0.5, "rds_qg": 0.2, "rds_coss": 0.3},
        "buck": {"rds_qg": 0.6, "rds_qoss": 0.3, "rds_coss": 0.1},
        "boost": {"rds_qg": 0.5, "rds_qoss": 0.3, "rds_coss": 0.2},
        "flyback": {"rds_qg": 0.5, "rds_qoss": 0.2, "rds_coss": 0.3},
        "full_bridge": {"rds_qg": 0.4, "rds_qoss": 0.4, "rds_coss": 0.2},
    }

    def calculate_foms(self, params: dict, price: float | None = None) -> FOMs:
        """Calculate all applicable FOMs.

        Args:
            params: Dict with keys like Rds_on (mΩ), Qg (nC), Qoss (nC),
                    Coss (pF), Eoss (µJ), Vds_max (V).
            price: Unit price in USD for FOM/$ calculation.

        Returns:
            FOMs dataclass with calculated values.
        """
        foms = FOMs()
        rds = params.get("Rds_on")  # mΩ

        if rds is not None:
            qg = params.get("Qg")  # nC
            if qg is not None:
                foms.rds_qg = rds * qg
                if price and price > 0:
                    foms.rds_qg_per_dollar = foms.rds_qg / price

            # Qoss: use directly if available, otherwise estimate from Eoss
            qoss = params.get("Qoss")  # nC
            if qoss is None:
                eoss = params.get("Eoss")  # µJ
                vds = params.get("Vds_max")  # V
                if eoss is not None and vds is not None and vds > 0:
                    # Qoss ≈ 2 × Eoss / Vds (nonlinear capacitance approx)
                    qoss = 2 * eoss * 1000 / vds  # µJ→nJ, /V → nC
            if qoss is not None:
                foms.rds_qoss = rds * qoss

            coss = params.get("Coss")  # pF
            if coss is not None:
                foms.rds_coss = rds * coss

        return foms

    def estimate_losses(self, params: dict, op: dict) -> LossEstimate:
        """Estimate power losses at a given operating point.

        Args:
            params: MOSFET parameters dict
                Rds_on: mΩ, Qg: nC, Eoss: µJ, Vsd: V, trr: ns, Qrr: nC
            op: Operating point dict
                i_rms: A, vgs: V, fsw: Hz, duty: float (0-1),
                power: W (for efficiency calc), i_peak: A (optional)

        Returns:
            LossEstimate with breakdown.
        """
        loss = LossEstimate()

        rds = params.get("Rds_on", 0) / 1000.0  # Ω
        i_rms = op.get("i_rms", 0)
        fsw = op.get("fsw", 100e3)
        vgs = op.get("vgs", 15)
        duty = op.get("duty", 0.5)
        power = op.get("power", 0)

        # Conduction loss: I²rms × Rds(on)
        loss.P_cond = i_rms ** 2 * rds

        # Gate drive loss: Qg × Vgs × Fsw
        qg = params.get("Qg", 0) * 1e-9  # C
        loss.P_gate = qg * vgs * fsw

        # Switching loss
        eoss = params.get("Eoss")
        if eoss is not None:
            loss.P_sw = eoss * 1e-6 * fsw  # Eoss × Fsw
        else:
            # Approximate from Qg
            loss.P_sw = 0.5 * qg * vgs * fsw

        # Body diode conduction (simplified)
        vsd = params.get("Vsd", 0.7)  # V
        trr = params.get("trr", 0)  # ns
        i_peak = op.get("i_peak", i_rms)
        if trr > 0:
            loss.P_body_diode = vsd * i_peak * trr * 1e-9 * fsw

        loss.P_total = loss.P_cond + loss.P_sw + loss.P_gate + loss.P_body_diode

        if power > 0:
            loss.eta_impact = (loss.P_total / power) * 100  # % of power

        return loss

    def rank_candidates(self, candidates: list, topology: str = "dab",
                        operating_point: dict | None = None) -> list:
        """Rank MOSFET candidates by weighted FOM score.

        Args:
            candidates: List of dicts, each with 'params' and optional 'price',
                        'part_number', 'manufacturer'.
            topology: Converter topology for FOM weighting.
            operating_point: Optional op point for loss estimation.

        Returns:
            Sorted list with FOMs, losses, and composite score.
        """
        weights = self.TOPOLOGY_WEIGHTS.get(topology, self.TOPOLOGY_WEIGHTS["dab"])
        results = []

        for cand in candidates:
            params = cand.get("params", cand)
            price = cand.get("price")
            foms = self.calculate_foms(params, price)

            result = {
                "part_number": cand.get("part_number", cand.get("ManufacturerPartNumber", "?")),
                "manufacturer": cand.get("manufacturer", cand.get("Manufacturer", "?")),
                "price": price,
                "foms": foms,
                "params": params,
            }

            # Calculate weighted score (lower is better)
            score = 0
            score_parts = 0
            if foms.rds_qg is not None and "rds_qg" in weights:
                score += foms.rds_qg * weights["rds_qg"]
                score_parts += weights["rds_qg"]
            if foms.rds_qoss is not None and "rds_qoss" in weights:
                score += foms.rds_qoss * weights["rds_qoss"]
                score_parts += weights["rds_qoss"]
            if foms.rds_coss is not None and "rds_coss" in weights:
                score += foms.rds_coss * weights["rds_coss"]
                score_parts += weights["rds_coss"]

            result["composite_score"] = score / score_parts if score_parts > 0 else float("inf")

            if operating_point:
                result["losses"] = self.estimate_losses(params, operating_point)

            results.append(result)

        results.sort(key=lambda r: r["composite_score"])
        return results

    def gate_driver_requirements(self, params: dict, fsw: float = 100e3) -> GateDriverReqs:
        """Calculate gate driver requirements from MOSFET parameters.

        Args:
            params: MOSFET parameters (Qg in nC, Ciss in pF, etc.)
            fsw: Switching frequency [Hz]

        Returns:
            GateDriverReqs with current, dead-time, and power requirements.
        """
        reqs = GateDriverReqs()
        qg = params.get("Qg", 100)  # nC
        vgs = 15  # default gate voltage
        target_tr = 20  # target rise time in ns

        # SiC detection
        is_sic = "sic" in str(params.get("technology", "")).lower()
        if is_sic:
            vgs = 18
            target_tr = 15

        # Minimum peak current: I = Qg / t_rise
        reqs.min_peak_current_A = (qg * 1e-9) / (target_tr * 1e-9)
        reqs.recommended_current_A = reqs.min_peak_current_A * 1.5

        # Dead time calculation
        # Minimum: based on driver propagation delay skew + MOSFET turn-off
        coss = params.get("Coss", 500)  # pF
        rds = params.get("Rds_on", 50)  # mΩ
        # Approximate MOSFET turn-off time from Coss and Rds
        t_off_approx = coss * 1e-12 * (rds / 1000.0) * 1e9  # ns (very rough)
        reqs.min_dead_time_ns = max(20, t_off_approx + 20)  # +20ns safety
        reqs.max_dead_time_ns = reqs.min_dead_time_ns + 100

        # Gate drive power
        reqs.gate_power_W = qg * 1e-9 * vgs * fsw
        reqs.recommended_vcc = vgs

        # Notes
        if is_sic:
            reqs.notes.append("SiC MOSFET: use Vcc=18V for lowest Rds(on)")
            reqs.notes.append("Negative turn-off voltage (-2V to -5V) recommended for SiC")
        if qg > 150:
            reqs.notes.append(f"High Qg ({qg}nC): ensure driver peak current > {reqs.min_peak_current_A:.1f}A")
        if fsw > 200e3:
            reqs.notes.append(f"High Fsw ({fsw/1e3:.0f}kHz): gate drive loss = {reqs.gate_power_W:.2f}W per device")

        return reqs

    def voltage_derating(self, vds_max: float, vin: float,
                         safety_factor: float = 0.8) -> dict:
        """Check voltage derating.

        Args:
            vds_max: MOSFET max Vds [V]
            vin: Application voltage [V]
            safety_factor: Derating factor (0.8 = 80%)

        Returns:
            Dict with derating analysis.
        """
        derated = vds_max * safety_factor
        margin = (derated - vin) / vin * 100

        return {
            "vds_max": vds_max,
            "derated_voltage": derated,
            "application_voltage": vin,
            "margin_pct": margin,
            "pass": margin > 0,
            "recommendation": (
                f"OK: {margin:.0f}% margin after {safety_factor*100:.0f}% derating"
                if margin > 0
                else f"FAIL: need Vds > {vin/safety_factor:.0f}V for {safety_factor*100:.0f}% derating"
            ),
        }

    def thermal_check(self, params: dict, op: dict,
                      t_ambient: float = 40.0) -> dict:
        """Thermal analysis.

        Args:
            params: MOSFET params with RthJC, RthJA, Pd_max
            op: Operating point (for loss estimation)
            t_ambient: Ambient temperature [°C]

        Returns:
            Dict with thermal analysis.
        """
        losses = self.estimate_losses(params, op)
        rth_jc = params.get("RthJC", 0.5)  # °C/W
        rth_ja = params.get("RthJA", 40)    # °C/W
        pd_max = params.get("Pd_max", 200)  # W
        tj_max = 175  # typical for SiC

        tj_no_heatsink = t_ambient + losses.P_total * rth_ja
        tj_with_heatsink = t_ambient + losses.P_total * rth_jc

        # Required heatsink thermal resistance
        if losses.P_total > 0:
            rth_hs_max = (tj_max - t_ambient) / losses.P_total - rth_jc
        else:
            rth_hs_max = float("inf")

        return {
            "P_total_W": losses.P_total,
            "Tj_no_heatsink_C": tj_no_heatsink,
            "Tj_with_ideal_heatsink_C": tj_with_heatsink,
            "Tj_max_C": tj_max,
            "needs_heatsink": tj_no_heatsink > tj_max,
            "max_heatsink_RthSA": max(0, rth_hs_max),
            "thermal_margin_C": tj_max - tj_with_heatsink,
        }
