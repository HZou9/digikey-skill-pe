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
        "cllc": {"rds_qoss": 0.5, "rds_qg": 0.2, "rds_coss": 0.3},
        "buck": {"rds_qg": 0.6, "rds_qoss": 0.3, "rds_coss": 0.1},
        "boost": {"rds_qg": 0.5, "rds_qoss": 0.3, "rds_coss": 0.2},
        "flyback": {"rds_qg": 0.5, "rds_qoss": 0.2, "rds_coss": 0.3},
        "full_bridge": {"rds_qg": 0.4, "rds_qoss": 0.4, "rds_coss": 0.2},
        "npc": {"rds_qg": 0.35, "rds_qoss": 0.45, "rds_coss": 0.2},
        "t_type": {"rds_qg": 0.35, "rds_qoss": 0.45, "rds_coss": 0.2},
        "pfc": {"rds_qg": 0.5, "rds_qoss": 0.35, "rds_coss": 0.15},
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

        NOTE: This is a simplified estimate. Actual losses depend on topology,
        modulation, dead time, parasitics, etc. Use this only as a rough
        reference — users should calculate losses for their specific application.

        Args:
            params: MOSFET parameters dict
                Rds_on: mΩ, Qg: nC, Eoss: µJ, Vsd: V, trr: ns
            op: Operating point dict
                i_rms: A, vgs: V, fsw: Hz, duty: float, power: W

        Returns:
            LossEstimate with breakdown.
        """
        loss = LossEstimate()

        rds = params.get("Rds_on", 0) / 1000.0  # Ω
        i_rms = op.get("i_rms", 0)
        fsw = op.get("fsw", 100e3)
        vgs = op.get("vgs", 15)
        power = op.get("power", 0)

        loss.P_cond = i_rms ** 2 * rds
        qg = params.get("Qg", 0) * 1e-9
        loss.P_gate = qg * vgs * fsw

        eoss = params.get("Eoss")
        if eoss is not None:
            loss.P_sw = eoss * 1e-6 * fsw
        else:
            loss.P_sw = 0.5 * qg * vgs * fsw

        vsd = params.get("Vsd", 0.7)
        trr = params.get("trr", 0)
        i_peak = op.get("i_peak", i_rms)
        if trr > 0:
            loss.P_body_diode = vsd * i_peak * trr * 1e-9 * fsw

        loss.P_total = loss.P_cond + loss.P_sw + loss.P_gate + loss.P_body_diode
        if power > 0:
            loss.eta_impact = (loss.P_total / power) * 100

        return loss

    def rank_candidates(self, candidates: list, topology: str = "dab") -> list:
        """Rank MOSFET candidates by weighted FOM score.

        Provides FOM-based ranking as a reference. Loss calculation and final
        device selection should be done by the user based on their specific
        topology, modulation, and operating conditions.

        Args:
            candidates: List of dicts, each with 'params' and optional 'price',
                        'part_number', 'manufacturer'.
            topology: Converter topology for FOM weighting.

        Returns:
            Sorted list with FOMs and composite score (lower = better FOM).
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
            reqs.notes.append("SiC MOSFET: Vcc=18V typical for lowest Rds(on)")
            reqs.notes.append("SiC turn-off options: -5V/-2V (negative rail), 0V (with active Miller clamp)")
        if qg > 150:
            reqs.notes.append(f"High Qg ({qg}nC): ensure driver peak current > {reqs.min_peak_current_A:.1f}A")
        if fsw > 200e3:
            reqs.notes.append(f"High Fsw ({fsw/1e3:.0f}kHz): gate drive loss = {reqs.gate_power_W:.2f}W per device")

        return reqs

    def calculate_gate_resistors(self, params: dict,
                                  driver_io_source: float = 4.0,
                                  driver_io_sink: float = 6.0,
                                  fsw: float = 100e3) -> dict:
        """Calculate recommended gate resistors for turn-on/turn-off.

        Args:
            params: MOSFET parameters (Qg nC, Ciss pF, technology, etc.)
            driver_io_source: Driver source current [A]
            driver_io_sink: Driver sink current [A]
            fsw: Switching frequency [Hz]

        Returns:
            Dict with Rg_on, Rg_off, and switching time estimates.
        """
        qg = params.get("Qg", 100)  # nC
        ciss = params.get("Ciss", 2000)  # pF
        is_sic = "sic" in str(params.get("technology", "")).lower()

        vgs = 18 if is_sic else 12
        vgs_off = -5 if is_sic else 0  # negative turn-off for SiC

        # Target switching times
        # SiC: faster switching (10-20ns), Si: 20-50ns
        target_t_on_ns = 15 if is_sic else 30
        target_t_off_ns = 20 if is_sic else 40

        # Rg = (Vgs - Vplateau) * t_sw / Qg
        # Simplified: Rg ≈ Vgs * t_sw / Qg (conservative)
        v_plateau = params.get("Vgs_th", 4.0) * 1.2  # ~1.2x threshold
        v_drive_on = vgs - v_plateau
        v_drive_off = v_plateau - vgs_off

        # Calculate Rg from target switching time
        # t_sw ≈ Qg * Rg / Vdrive → Rg = t_sw * Vdrive / Qg
        rg_on_ideal = target_t_on_ns * v_drive_on / qg  # Ω
        rg_off_ideal = target_t_off_ns * v_drive_off / qg  # Ω

        # Limit by driver current capability: Rg_min = Vgs / Io
        rg_on_min = vgs / driver_io_source
        rg_off_min = (vgs - vgs_off) / driver_io_sink

        rg_on = max(rg_on_ideal, rg_on_min)
        rg_off = max(rg_off_ideal, rg_off_min)

        # Round to standard resistor values (E24 series)
        rg_on = self._nearest_e24(rg_on)
        rg_off = self._nearest_e24(rg_off)

        # Recalculate actual switching times
        t_on_actual = qg * rg_on / v_drive_on if v_drive_on > 0 else 0
        t_off_actual = qg * rg_off / v_drive_off if v_drive_off > 0 else 0

        # Gate resistor power dissipation: P = Qg * Vgs * Fsw (split between Rg and driver)
        p_rg_on = 0.5 * qg * 1e-9 * vgs * fsw * (rg_on / (rg_on + 1))
        p_rg_off = 0.5 * qg * 1e-9 * abs(vgs - vgs_off) * fsw * (rg_off / (rg_off + 1))

        notes = []
        if is_sic:
            notes.append("SiC: use separate Rg_on/Rg_off with diode steering")
            notes.append(f"Negative turn-off voltage ({vgs_off}V) recommended")
        if t_on_actual < 10:
            notes.append(f"Warning: very fast turn-on ({t_on_actual:.0f}ns), check dv/dt and EMI")
        if t_off_actual < 10:
            notes.append(f"Warning: very fast turn-off ({t_off_actual:.0f}ns), check voltage overshoot")

        return {
            "Rg_on_ohm": rg_on,
            "Rg_off_ohm": rg_off,
            "t_on_ns": round(t_on_actual, 1),
            "t_off_ns": round(t_off_actual, 1),
            "P_rg_on_W": round(p_rg_on, 3),
            "P_rg_off_W": round(p_rg_off, 3),
            "Vgs_on": vgs,
            "Vgs_off": vgs_off,
            "notes": notes,
        }

    @staticmethod
    def _nearest_e24(value: float) -> float:
        """Round to nearest E24 standard resistor value."""
        e24 = [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
               3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1]
        if value <= 0:
            return 1.0
        decade = 1
        while value >= 10:
            value /= 10
            decade *= 10
        while value < 1:
            value *= 10
            decade /= 10
        closest = min(e24, key=lambda x: abs(x - value))
        return closest * decade

    def bootstrap_capacitor(self, params: dict, duty_max: float = 0.95,
                            fsw: float = 100e3, vcc: float = 15.0) -> dict:
        """Calculate bootstrap capacitor for high-side gate driver.

        Args:
            params: MOSFET parameters (Qg nC, etc.)
            duty_max: Maximum duty cycle (worst case for bootstrap)
            fsw: Switching frequency [Hz]
            vcc: Driver supply voltage [V]

        Returns:
            Dict with Cboot minimum, recommended value, and voltage rating.
        """
        qg = params.get("Qg", 100)  # nC
        is_sic = "sic" in str(params.get("technology", "")).lower()

        # Bootstrap capacitor must supply gate charge + quiescent current
        i_qcc = 5e-3  # driver quiescent current (typical 1-5mA)
        v_diode_drop = 0.7  # bootstrap diode forward voltage
        v_level_shift = 0.3  # level-shift losses

        # Time high-side is on (worst case = max duty)
        t_on_max = duty_max / fsw

        # Total charge needed per cycle
        q_total = qg * 1e-9 + i_qcc * t_on_max  # Coulombs

        # Allow max 0.5V ripple on bootstrap cap
        v_ripple_max = 0.5
        c_boot_min = q_total / v_ripple_max  # Farads

        # Recommended: 10x minimum for margin
        c_boot_rec = c_boot_min * 10

        # Voltage rating: Vcc + margin
        v_rating = vcc + v_diode_drop + 10  # extra 10V margin

        # Standard capacitor values
        c_min_uf = c_boot_min * 1e6
        c_rec_uf = c_boot_rec * 1e6

        # Round up to standard value
        standard_caps_uf = [0.1, 0.22, 0.47, 1.0, 2.2, 4.7, 10, 22, 47, 100]
        c_selected = next((c for c in standard_caps_uf if c >= c_rec_uf), standard_caps_uf[-1])

        notes = []
        notes.append("Use low-ESR ceramic (X7R or C0G) capacitor")
        notes.append(f"Bootstrap diode: fast recovery (<50ns), Vr ≥ {int(v_rating)}V")
        if is_sic:
            notes.append("SiC: consider dedicated isolated supply instead of bootstrap for duty >90%")
        if duty_max > 0.9:
            notes.append(f"High duty ({duty_max*100:.0f}%): ensure bootstrap refresh time is sufficient")

        return {
            "C_boot_min_uF": round(c_min_uf, 3),
            "C_boot_recommended_uF": round(c_selected, 1),
            "V_rating_min": round(v_rating),
            "V_ripple_max_V": v_ripple_max,
            "dielectric": "X7R",
            "duty_max": duty_max,
            "notes": notes,
        }

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
