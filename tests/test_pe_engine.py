"""Unit tests for the PE engine components."""
import pytest
import os
from pathlib import Path

# Ensure pe_engine is importable
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from pe_engine.fom import FOMCalculator, FOMs, LossEstimate, GateDriverReqs
from pe_engine.datasheet_parser import (
    DatasheetParser, parse_datasheet, parse_from_digikey_params,
    _normalize_unit, _parse_number,
)
from pe_engine.selector import PowerComponentSelector
from pe_engine.bom import BOMOptimizer


# ---- FOM Calculator Tests ----

class TestFOMCalculator:

    def setup_method(self):
        self.calc = FOMCalculator()

    def test_basic_fom_rds_qg(self):
        params = {"Rds_on": 25, "Qg": 100}
        foms = self.calc.calculate_foms(params)
        assert foms.rds_qg == 2500  # 25 * 100

    def test_fom_rds_qoss_direct(self):
        params = {"Rds_on": 25, "Qoss": 50}
        foms = self.calc.calculate_foms(params)
        assert foms.rds_qoss == 1250  # 25 * 50

    def test_fom_rds_qoss_from_eoss(self):
        """Qoss estimated from Eoss when Qoss not provided."""
        params = {"Rds_on": 25, "Eoss": 37, "Vds_max": 650}
        foms = self.calc.calculate_foms(params)
        assert foms.rds_qoss is not None
        # Qoss ≈ 2 * 37 * 1000 / 650 ≈ 113.8 nC
        # FOM ≈ 25 * 113.8 ≈ 2846
        assert 2800 < foms.rds_qoss < 2900

    def test_fom_rds_coss(self):
        params = {"Rds_on": 25, "Coss": 178}
        foms = self.calc.calculate_foms(params)
        assert foms.rds_coss == 4450

    def test_fom_per_dollar(self):
        params = {"Rds_on": 25, "Qg": 100}
        foms = self.calc.calculate_foms(params, price=10.0)
        assert foms.rds_qg_per_dollar == 250.0

    def test_fom_no_rds(self):
        params = {"Qg": 100}
        foms = self.calc.calculate_foms(params)
        assert foms.rds_qg is None
        assert foms.rds_qoss is None

    def test_loss_estimation(self):
        params = {"Rds_on": 25, "Qg": 100, "Eoss": 37}
        op = {"i_rms": 10, "vgs": 15, "fsw": 100e3, "power": 5000}
        loss = self.calc.estimate_losses(params, op)
        assert loss.P_cond > 0  # I^2 * R
        assert loss.P_gate > 0  # Qg * Vgs * Fsw
        assert loss.P_sw > 0    # Eoss * Fsw
        assert loss.P_total == pytest.approx(
            loss.P_cond + loss.P_sw + loss.P_gate + loss.P_body_diode)

    def test_loss_conduction(self):
        """P_cond = I_rms^2 * Rds_on"""
        params = {"Rds_on": 25, "Qg": 0}
        op = {"i_rms": 10, "vgs": 15, "fsw": 100e3, "power": 5000}
        loss = self.calc.estimate_losses(params, op)
        # P_cond = 10^2 * 0.025 = 2.5 W
        assert loss.P_cond == pytest.approx(2.5)

    def test_voltage_derating_pass(self):
        result = self.calc.voltage_derating(650, 400)
        assert result["pass"] is True
        assert result["margin_pct"] > 0

    def test_voltage_derating_fail(self):
        result = self.calc.voltage_derating(400, 400)
        assert result["pass"] is False

    def test_gate_driver_requirements(self):
        params = {"Qg": 100, "Coss": 200, "Rds_on": 25}
        reqs = self.calc.gate_driver_requirements(params, fsw=100e3)
        assert reqs.min_peak_current_A > 0
        assert reqs.recommended_current_A > reqs.min_peak_current_A
        assert reqs.gate_power_W > 0
        assert reqs.min_dead_time_ns > 0

    def test_gate_driver_sic(self):
        params = {"Qg": 100, "Coss": 200, "Rds_on": 25, "technology": "SiC"}
        reqs = self.calc.gate_driver_requirements(params)
        assert reqs.recommended_vcc == 18
        assert any("SiC" in n for n in reqs.notes)

    def test_rank_candidates(self):
        candidates = [
            {"part_number": "A", "params": {"Rds_on": 30, "Qg": 120, "Coss": 200}, "price": 8},
            {"part_number": "B", "params": {"Rds_on": 25, "Qg": 100, "Coss": 180}, "price": 10},
        ]
        ranked = self.calc.rank_candidates(candidates, "dab")
        assert len(ranked) == 2
        assert all("composite_score" in r for r in ranked)
        # B should rank better (lower FOM)
        assert ranked[0]["part_number"] == "B"

    def test_thermal_check(self):
        params = {"Rds_on": 25, "Qg": 100, "RthJC": 0.5, "RthJA": 40, "Pd_max": 200}
        op = {"i_rms": 10, "vgs": 15, "fsw": 100e3, "power": 5000}
        result = self.calc.thermal_check(params, op)
        assert "P_total_W" in result
        assert "max_heatsink_RthSA" in result
        assert result["Tj_max_C"] == 175


# ---- Unit normalization tests ----

class TestUnitNormalization:

    def test_mohm(self):
        val, unit = _normalize_unit(25, "mΩ")
        assert val == 25
        assert unit == "mOhm"

    def test_ohm_to_mohm(self):
        val, unit = _normalize_unit(0.025, "Ω")
        assert val == 25
        assert unit == "mOhm"

    def test_nc(self):
        val, unit = _normalize_unit(100, "nC")
        assert val == 100
        assert unit == "nC"

    def test_pf(self):
        val, unit = _normalize_unit(2000, "pF")
        assert val == 2000
        assert unit == "pF"

    def test_nf_to_pf(self):
        val, unit = _normalize_unit(2, "nF")
        assert val == 2000
        assert unit == "pF"

    def test_uj(self):
        val, unit = _normalize_unit(37, "µJ")
        assert val == 37
        assert unit == "uJ"

    def test_kv(self):
        val, unit = _normalize_unit(5.7, "kV")
        assert val == 5700
        assert unit == "V"

    def test_parse_number(self):
        assert _parse_number("25") == 25
        assert _parse_number("3.14") == 3.14
        assert _parse_number("-40") == -40
        assert _parse_number("1e3") == 1000
        assert _parse_number("—") is None
        assert _parse_number("-") is None


# ---- Datasheet Parser Tests ----

class TestDatasheetParser:

    @pytest.fixture
    def parser(self):
        return DatasheetParser()

    def test_detect_mosfet(self, parser):
        text = "SiC MOSFET N-Channel RDS(on) drain-source gate charge VDSS"
        assert parser._detect_type(text) == "mosfet"

    def test_detect_gate_driver(self, parser):
        text = "gate driver half bridge isolated propagation delay dead time"
        assert parser._detect_type(text) == "gate_driver"

    def test_manufacturer_detection(self, parser):
        assert parser._extract_manufacturer("Texas Instruments UCC21530") == "Texas Instruments"
        assert parser._extract_manufacturer("ROHM Semiconductor") == "ROHM"
        assert parser._extract_manufacturer("Wolfspeed C3M0025065K") == "Wolfspeed"

    def test_manufacturer_priority(self, parser):
        """TI should be detected even if Wolfspeed/Cree is mentioned later."""
        text = "Texas Instruments\ncompatible with Wolfspeed SiC MOSFETs"
        assert parser._extract_manufacturer(text) == "Texas Instruments"

    def test_merge_header_rows(self, parser):
        row1 = ["Symbol", "Conditions", "Values", None, None]
        row2 = [None, None, "Min.", "Typ.", "Max."]
        merged = parser._merge_header_rows(row1, row2)
        assert merged == ["Symbol", "Conditions", "Min.", "Typ.", "Max."]

    def test_identify_columns_standard(self, parser):
        header = ["Parameter", "Symbol", "Min.", "Typ.", "Max.", "Unit"]
        col_map = parser._identify_columns(header)
        assert "param" in col_map
        assert "symbol" in col_map
        assert "min" in col_map
        assert "typ" in col_map
        assert "max" in col_map
        assert "unit" in col_map

    def test_identify_columns_symbol_only(self, parser):
        """ROHM-style: Symbol as first column, no Parameter column."""
        header = ["Symbol", "Conditions", "Min.", "Typ.", "Max."]
        col_map = parser._identify_columns(header)
        assert "param" in col_map  # symbol promoted to param
        assert "min" in col_map

    def test_parse_from_digikey_params(self):
        params = [
            {"Name": "Rds On (Max) @ Id, Vgs", "Value": "25mΩ @ 50A, 15V"},
            {"Name": "Gate Charge (Qg) (Max) @ Vgs", "Value": "95nC @ 15V"},
            {"Name": "Drain-Source Voltage (Vdss)", "Value": "650V"},
            {"Name": "Technology", "Value": "SiC (Silicon Carbide)"},
        ]
        specs = parse_from_digikey_params(params)
        assert specs["Rds_on"] == 25
        assert specs["Qg"] == 95
        assert specs["Vds_max"] == 650
        assert "SiC" in specs["technology"]


# ---- PDF Parser Tests (require sample datasheets) ----

SAMPLE_DIR = Path(__file__).parent / "sample_datasheets"


@pytest.mark.skipif(
    not (SAMPLE_DIR / "C3M0025065K.pdf").exists(),
    reason="Sample datasheet not available"
)
class TestWolfspeedParser:

    @pytest.fixture
    def result(self):
        return parse_datasheet(str(SAMPLE_DIR / "C3M0025065K.pdf"))

    def test_type(self, result):
        assert result.component_type == "mosfet"

    def test_manufacturer(self, result):
        assert result.manufacturer == "Wolfspeed"

    def test_vds(self, result):
        assert result.get("Vds_max") == 650

    def test_rds_on(self, result):
        rds = result.get("Rds_on")
        assert rds is not None
        assert 20 < rds < 40  # 25-33 mΩ typical

    def test_qg(self, result):
        qg = result.get("Qg")
        assert qg is not None
        assert 90 < qg < 130

    def test_ciss(self, result):
        ciss = result.get("Ciss")
        assert ciss is not None
        assert 2000 < ciss < 4000

    def test_eoss(self, result):
        eoss = result.get("Eoss")
        assert eoss is not None
        assert 30 < eoss < 50


@pytest.mark.skipif(
    not (SAMPLE_DIR / "SCT3022AL.pdf").exists(),
    reason="Sample datasheet not available"
)
class TestROHMParser:

    @pytest.fixture
    def result(self):
        return parse_datasheet(str(SAMPLE_DIR / "SCT3022AL.pdf"))

    def test_type(self, result):
        assert result.component_type == "mosfet"

    def test_manufacturer(self, result):
        assert result.manufacturer == "ROHM"

    def test_vds(self, result):
        assert result.get("Vds_max") == 650

    def test_rds_on(self, result):
        rds = result.get("Rds_on")
        assert rds is not None
        assert 18 < rds < 30  # 22 mΩ typical

    def test_id_max(self, result):
        assert result.get("Id_max") == 93

    def test_qg(self, result):
        qg = result.get("Qg")
        assert qg is not None
        assert 120 < qg < 150  # 133 nC typical


@pytest.mark.skipif(
    not (SAMPLE_DIR / "UCC21530.pdf").exists(),
    reason="Sample datasheet not available"
)
class TestUCC21530Parser:

    @pytest.fixture
    def result(self):
        return parse_datasheet(str(SAMPLE_DIR / "UCC21530.pdf"))

    def test_type(self, result):
        assert result.component_type == "gate_driver"

    def test_manufacturer(self, result):
        assert result.manufacturer == "Texas Instruments"

    def test_io_source(self, result):
        io = result.get("Io_source")
        assert io is not None
        assert io >= 4.0

    def test_tpd(self, result):
        tpd = result.get("tpd_rise")
        assert tpd is not None
        assert 25 < tpd < 50

    def test_isolation(self, result):
        iso = result.get("isolation_voltage")
        assert iso is not None
        assert iso > 2000


# ---- Selector Tests (mock mode) ----

class TestPowerComponentSelector:

    def setup_method(self):
        self.sel = PowerComponentSelector()

    def test_mosfet_selection(self):
        result = self.sel.select_mosfet({
            "topology": "dab", "vin": 400, "vout": 48,
            "power": 5000, "fsw": 100e3,
        })
        assert "candidates" in result
        assert len(result["candidates"]) > 0
        assert result.get("mock") is True

    def test_gate_driver_selection(self):
        result = self.sel.select_gate_driver(
            {"Qg": 95, "Coss": 500, "Rds_on": 25},
            topology="dab", fsw=100e3,
        )
        assert "candidates" in result
        assert len(result["candidates"]) > 0

    def test_power_module_selection(self):
        result = self.sel.select_power_module({
            "topology": "dab", "vin": 800, "vout": 400,
            "power": 100000, "fsw": 50e3, "cooling": "liquid",
        })
        assert "candidates" in result
        assert len(result["candidates"]) > 0
        # All should have thermal analysis
        for c in result["candidates"]:
            assert "thermal" in c
            assert "Tj_estimated_C" in c["thermal"]

    def test_heatsink_selection(self):
        result = self.sel.select_heatsink({
            "p_loss": 100, "rth_jc": 0.46,
            "tj_max": 175, "t_ambient": 40,
            "cooling": "forced_air",
        })
        assert "candidates" in result
        assert result["rth_sa_required"] > 0
        for c in result["candidates"]:
            assert "thermal" in c

    def test_heatsink_impossible(self):
        """Too much power for any heatsink."""
        result = self.sel.select_heatsink({
            "p_loss": 10000, "rth_jc": 0.5,
            "tj_max": 175, "t_ambient": 40,
            "cooling": "natural",
        })
        assert "error" in result

    def test_mosfet_npc_topology(self):
        """NPC topology should work and return candidates."""
        result = self.sel.select_mosfet({
            "topology": "npc", "vin": 800, "vout": 400,
            "power": 10000, "fsw": 50e3,
        })
        assert "candidates" in result
        assert len(result["candidates"]) > 0

    def test_mosfet_pfc_topology(self):
        """PFC topology should work."""
        result = self.sel.select_mosfet({
            "topology": "pfc", "vin": 400, "vout": 800,
            "power": 3000, "fsw": 65e3,
        })
        assert "candidates" in result

    def test_mosfet_cllc_topology(self):
        """CLLC topology should work."""
        result = self.sel.select_mosfet({
            "topology": "cllc", "vin": 400, "vout": 400,
            "power": 5000, "fsw": 100e3,
        })
        assert "candidates" in result


# ---- Gate Resistor Tests ----

class TestGateResistor:

    def setup_method(self):
        self.calc = FOMCalculator()

    def test_basic_gate_resistor(self):
        params = {"Qg": 95, "Ciss": 2253, "technology": "Si"}
        result = self.calc.calculate_gate_resistors(params)
        assert result["Rg_on_ohm"] > 0
        assert result["Rg_off_ohm"] > 0
        assert result["t_on_ns"] > 0
        assert result["t_off_ns"] > 0

    def test_sic_gate_resistor(self):
        params = {"Qg": 95, "Ciss": 2253, "technology": "SiC"}
        result = self.calc.calculate_gate_resistors(params)
        assert result["Vgs_on"] == 18
        assert result["Vgs_off"] == -5
        assert any("SiC" in n for n in result["notes"])

    def test_gate_resistor_power(self):
        params = {"Qg": 95, "technology": "Si"}
        result = self.calc.calculate_gate_resistors(params, fsw=100e3)
        assert result["P_rg_on_W"] >= 0
        assert result["P_rg_off_W"] >= 0

    def test_e24_rounding(self):
        """Gate resistors should be E24 standard values."""
        e24 = [1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8, 2.0, 2.2, 2.4, 2.7, 3.0,
               3.3, 3.6, 3.9, 4.3, 4.7, 5.1, 5.6, 6.2, 6.8, 7.5, 8.2, 9.1]

        def is_e24(val):
            if val <= 0:
                return False
            while val >= 10:
                val /= 10
            while val < 1:
                val *= 10
            return any(abs(val - e) < 0.01 for e in e24)

        params = {"Qg": 95, "technology": "SiC"}
        result = self.calc.calculate_gate_resistors(params)
        assert is_e24(result["Rg_on_ohm"])
        assert is_e24(result["Rg_off_ohm"])


# ---- Bootstrap Capacitor Tests ----

class TestBootstrapCapacitor:

    def setup_method(self):
        self.calc = FOMCalculator()

    def test_basic_bootstrap(self):
        params = {"Qg": 95, "technology": "Si"}
        result = self.calc.bootstrap_capacitor(params)
        assert result["C_boot_min_uF"] > 0
        assert result["C_boot_recommended_uF"] >= result["C_boot_min_uF"]
        assert result["V_rating_min"] > 15  # must be > Vcc

    def test_sic_bootstrap(self):
        params = {"Qg": 95, "technology": "SiC"}
        result = self.calc.bootstrap_capacitor(params)
        assert any("SiC" in n for n in result["notes"])

    def test_high_duty_warning(self):
        params = {"Qg": 95, "technology": "Si"}
        result = self.calc.bootstrap_capacitor(params, duty_max=0.95)
        assert any("High duty" in n for n in result["notes"])

    def test_standard_cap_value(self):
        """Selected cap should be a standard value."""
        standard = [0.1, 0.22, 0.47, 1.0, 2.2, 4.7, 10, 22, 47, 100]
        params = {"Qg": 95}
        result = self.calc.bootstrap_capacitor(params)
        assert result["C_boot_recommended_uF"] in standard


# ---- BOM Tests ----

class TestBOMOptimizer:

    def setup_method(self):
        self.bom = BOMOptimizer()

    def test_pricing_analysis(self):
        result = self.bom.pricing_analysis("C3M0025065K-ND")
        assert "tiers" in result
        assert len(result["tiers"]) > 0
        assert result["sweet_spot_qty"] >= 1
        assert result["unit_price_1pc"] is not None

    def test_check_stock(self):
        result = self.bom.check_stock("C3M0025065K-ND", required_qty=10)
        assert "stock" in result
        assert "status" in result
        assert result["status"] in ("ok", "warning", "low", "critical")
        assert "covers_demand" in result

    def test_optimize_bom(self):
        components = [
            {"part_number": "C3M0025065K", "digikey_pn": "C3M0025065K-ND",
             "quantity_per_unit": 4},
        ]
        result = self.bom.optimize_bom(components, quantity=10)
        assert result["quantity"] == 10
        assert len(result["components"]) == 1
        assert result["total_bom_cost"] > 0
        assert result["cost_per_unit"] > 0
        # Stock check should be included by default
        assert "stock" in result["components"][0]

    def test_optimize_bom_no_stock(self):
        components = [
            {"part_number": "C3M0025065K", "digikey_pn": "C3M0025065K-ND",
             "quantity_per_unit": 4},
        ]
        result = self.bom.optimize_bom(components, quantity=10, check_inventory=False)
        assert "stock" not in result["components"][0]


# ---- CSV Export Tests ----

class TestCSVExport:

    def test_bom_csv_export(self):
        from pe_engine.bom import bom_to_csv
        bom_result = {
            "quantity": 100,
            "components": [
                {"part_number": "C3M0025065K", "digikey_pn": "C3M0025065K-ND",
                 "qty_per_unit": 4, "total_qty": 400,
                 "unit_price": 8.50, "line_cost": 3400.0,
                 "stock": 4523, "stock_status": "ok"},
            ],
            "total_bom_cost": 3400.0,
            "cost_per_unit": 34.0,
        }
        csv_str = bom_to_csv(bom_result)
        assert "part_number" in csv_str  # header
        assert "C3M0025065K" in csv_str
        assert "TOTAL" in csv_str
        lines = csv_str.strip().split("\n")
        assert len(lines) == 3  # header + 1 component + total

    def test_mosfet_csv_export(self):
        from pe_engine.bom import mosfet_selection_to_csv
        sel = PowerComponentSelector()
        result = sel.select_mosfet({
            "topology": "dab", "vin": 400, "vout": 48,
            "power": 5000, "fsw": 100e3,
        })
        csv_str = mosfet_selection_to_csv(result)
        assert "part_number" in csv_str
        assert "FOM_RdsQg" in csv_str
        lines = csv_str.strip().split("\n")
        assert len(lines) > 1  # header + at least 1 candidate

    def test_csv_file_export(self, tmp_path):
        from pe_engine.bom import bom_to_csv
        bom_result = {
            "quantity": 10,
            "components": [
                {"part_number": "TEST", "digikey_pn": "TEST-ND",
                 "qty_per_unit": 1, "total_qty": 10,
                 "unit_price": 1.0, "line_cost": 10.0},
            ],
            "total_bom_cost": 10.0,
        }
        out_file = str(tmp_path / "test_bom.csv")
        result = bom_to_csv(bom_result, out_file)
        assert result == out_file
        assert Path(out_file).exists()
        content = Path(out_file).read_text()
        assert "TEST" in content


# ---- Topology Weights Tests ----

class TestTopologyWeights:

    def test_npc_weights_exist(self):
        assert "npc" in FOMCalculator.TOPOLOGY_WEIGHTS
        w = FOMCalculator.TOPOLOGY_WEIGHTS["npc"]
        assert abs(sum(w.values()) - 1.0) < 0.01

    def test_cllc_weights_exist(self):
        assert "cllc" in FOMCalculator.TOPOLOGY_WEIGHTS

    def test_pfc_weights_exist(self):
        assert "pfc" in FOMCalculator.TOPOLOGY_WEIGHTS
        w = FOMCalculator.TOPOLOGY_WEIGHTS["pfc"]
        # PFC is hard-switched → rds_qg should dominate
        assert w["rds_qg"] >= w["rds_qoss"]

    def test_t_type_weights_exist(self):
        assert "t_type" in FOMCalculator.TOPOLOGY_WEIGHTS

    def test_all_weights_sum_to_one(self):
        for topo, w in FOMCalculator.TOPOLOGY_WEIGHTS.items():
            assert abs(sum(w.values()) - 1.0) < 0.01, f"{topo} weights sum to {sum(w.values())}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
