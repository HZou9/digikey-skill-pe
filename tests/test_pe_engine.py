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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
