#!/usr/bin/env python3
"""Power electronics component selection CLI - called by /pe-select skill."""
import argparse
import json
import sys
from pathlib import Path

PE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PE_ROOT))

from pe_engine.selector import PowerComponentSelector
from pe_engine.fom import FOMCalculator
from pe_engine.datasheet_parser import parse_datasheet, parse_from_digikey_params
from pe_engine.bom import BOMOptimizer, bom_to_csv, mosfet_selection_to_csv
from pe_engine.symbol_fetcher import SymbolFetcher


def cmd_mosfet(args):
    """Select MOSFETs for a power converter."""
    sel = PowerComponentSelector()
    specs = {
        "topology": args.topology,
        "vin": args.vin,
        "vout": args.vout,
        "power": args.power,
        "fsw": args.fsw,
        "zvs": args.zvs,
        "budget": args.budget,
        "n_devices": args.n_devices,
    }

    result = sel.select_mosfet(specs)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    if args.csv:
        csv_out = mosfet_selection_to_csv(result, args.csv if args.csv != "stdout" else None)
        if args.csv == "stdout":
            print(csv_out)
        else:
            print(f"MOSFET selection exported to {csv_out}")
        return

    mock_tag = " [MOCK DATA]" if result.get("mock") else ""
    print(f"\n{'='*70}")
    print(f"MOSFET Selection for {args.topology.upper()} Converter{mock_tag}")
    print(f"  Vin={args.vin}V, Vout={args.vout}V, P={args.power}W, Fsw={args.fsw/1e3:.0f}kHz")
    print(f"  Search query: \"{result.get('query', '')}\"")
    print(f"{'='*70}\n")

    candidates = result.get("candidates", [])
    if not candidates:
        print(f"No candidates found. Error: {result.get('error', 'unknown')}")
        return

    # FOM table
    header = f"{'#':<3} {'Part':<20} {'Mfr':<15} {'Rds(mΩ)':<9} {'Qg(nC)':<8} {'FOM_Qg':<8} {'FOM_Qoss':<9} {'$':<7} {'Score'}"
    print(header)
    print("-" * len(header))

    for i, c in enumerate(candidates, 1):
        p = c.get("params", {})
        f = c.get("foms")
        print(
            f"{i:<3} {c['part_number']:<20} "
            f"{c['manufacturer'][:14]:<15} "
            f"{p.get('Rds_on', '-'):<9} "
            f"{p.get('Qg', '-'):<8} "
            f"{f.rds_qg or '-':<8} "
            f"{f.rds_qoss or '-':<9} "
            f"${c.get('price', 0):<6.2f} "
            f"{c.get('composite_score', 0):.0f}"
        )

    # Voltage derating
    print(f"\nVoltage Derating Check:")
    for c in candidates[:3]:
        vd = c.get("voltage_derating", {})
        if vd:
            status = "PASS" if vd.get("pass") else "FAIL"
            print(f"  {c['part_number']}: Vds={vd.get('vds_max')}V, "
                  f"derated={vd.get('derated_voltage'):.0f}V → [{status}] {vd.get('recommendation')}")

    # Recommendation
    rec = result.get("recommendation", {})
    if rec:
        print(f"\n{'='*70}")
        print("RECOMMENDATION:")
        print(rec.get("text", ""))
        print(f"{'='*70}")


def cmd_gate_driver(args):
    """Select gate drivers for a MOSFET."""
    sel = PowerComponentSelector()

    # Build MOSFET params from args
    mosfet_params = {
        "Qg": args.qg,
        "Ciss": args.ciss or 2000,
        "Coss": args.coss or 500,
        "Rds_on": args.rds or 25,
        "technology": "SiC" if args.sic else "Si",
    }

    result = sel.select_gate_driver(mosfet_params, args.topology, args.fsw)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    print(f"\n{'='*70}")
    print(f"Gate Driver Selection for {args.topology.upper()}")
    print(f"  MOSFET: Qg={args.qg}nC, {'SiC' if args.sic else 'Si'}")
    print(f"{'='*70}\n")

    reqs = result.get("mosfet_requirements", {})
    print("MOSFET-derived Requirements:")
    print(f"  Min peak current: {reqs.get('min_peak_current_A', 0):.1f}A")
    print(f"  Recommended: {reqs.get('recommended_current_A', 0):.1f}A")
    dt = reqs.get("dead_time_range_ns", (0, 0))
    print(f"  Dead time: {dt[0]:.0f} - {dt[1]:.0f} ns")
    print(f"  Gate power/device: {reqs.get('gate_power_W', 0):.2f}W")
    print(f"  Recommended Vcc: {reqs.get('recommended_vcc', 0)}V")
    for note in reqs.get("notes", []):
        print(f"  Note: {note}")

    candidates = result.get("candidates", [])
    if candidates:
        print(f"\n{'#':<3} {'Part':<22} {'Mfr':<20} {'Io':<8} {'tpd':<8} {'CMTI':<10} {'Score'}")
        print("-" * 75)
        for i, c in enumerate(candidates, 1):
            p = c.get("params", {})
            io = p.get("Io_source", "-")
            tpd = p.get("tpd", "-")
            cmti = p.get("CMTI", "-")
            print(
                f"{i:<3} {c['part_number']:<22} "
                f"{c['manufacturer'][:19]:<20} "
                f"{io:<8} "
                f"{tpd:<8} "
                f"{cmti:<10} "
                f"{c.get('compatibility_score', 0):.2f}"
            )


def cmd_parse(args):
    """Parse a datasheet PDF."""
    result = parse_datasheet(args.pdf)
    summary = result.summary()

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
        return

    print(f"\nDatasheet Parser Results: {args.pdf}")
    print(f"  Type: {result.component_type}")
    print(f"  Part: {result.part_number}")
    print(f"  Manufacturer: {result.manufacturer}")
    print(f"  Package: {result.package}")
    print(f"\nExtracted Parameters:")
    for key, param in result.params.items():
        conf = f"[{param.confidence}]"
        vals = []
        if param.min_val is not None:
            vals.append(f"min={param.min_val}")
        if param.typ_val is not None:
            vals.append(f"typ={param.typ_val}")
        if param.max_val is not None:
            vals.append(f"max={param.max_val}")
        val_str = ", ".join(vals) if vals else str(param.value)
        cond = f" ({param.conditions})" if param.conditions else ""
        print(f"  {key:<12}: {val_str} {param.unit} {conf}{cond}")

    if result.warnings:
        print(f"\nWarnings:")
        for w in result.warnings:
            print(f"  - {w}")


def cmd_fom(args):
    """Calculate FOMs for given parameters."""
    calc = FOMCalculator()
    params = {
        "Rds_on": args.rds,
        "Qg": args.qg,
        "Qoss": args.qoss,
        "Coss": args.coss,
    }
    foms = calc.calculate_foms(params, args.price)

    print(f"\nFOM Calculation:")
    print(f"  Rds(on) = {args.rds} mΩ, Qg = {args.qg} nC")
    if foms.rds_qg is not None:
        print(f"  Rds×Qg  = {foms.rds_qg:.0f} mΩ·nC")
    if foms.rds_qoss is not None:
        print(f"  Rds×Qoss = {foms.rds_qoss:.0f} mΩ·nC")
    if foms.rds_coss is not None:
        print(f"  Rds×Coss = {foms.rds_coss:.0f} mΩ·pF")
    if foms.rds_qg_per_dollar is not None:
        print(f"  FOM/$    = {foms.rds_qg_per_dollar:.0f} mΩ·nC/$")


def cmd_symbols(args):
    """Fetch symbols and footprints."""
    fetcher = SymbolFetcher(output_dir=args.output)
    formats = []
    if args.kicad:
        formats.append("kicad9")
    if args.altium:
        formats.append("altium")
    if not formats:
        formats = ["kicad9", "altium"]

    if args.batch:
        # Read BOM from JSON file
        with open(args.batch) as f:
            bom = json.load(f)
        result = fetcher.fetch_batch(bom, formats)
        report = fetcher.generate_manual_download_report(result)
        print(report)
    else:
        result = fetcher.fetch(args.part_number, args.manufacturer, formats)
        print(json.dumps(result, indent=2))


def cmd_power_module(args):
    """Select power modules for high-power converters."""
    sel = PowerComponentSelector()
    specs = {
        "topology": args.topology,
        "vin": args.vin,
        "vout": args.vout,
        "power": args.power,
        "fsw": args.fsw,
        "cooling": args.cooling,
        "budget": args.budget,
        "prefer_sic": args.sic if args.sic else None,
    }

    result = sel.select_power_module(specs)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    mock_tag = " [MOCK DATA]" if result.get("mock") else ""
    print(f"\n{'='*70}")
    print(f"Power Module Selection for {args.topology.upper()} Converter{mock_tag}")
    print(f"  Vin={args.vin}V, Vout={args.vout}V, P={args.power/1000:.0f}kW, "
          f"Fsw={args.fsw/1e3:.0f}kHz, Cooling={args.cooling}")
    print(f"  Search query: \"{result.get('query', '')}\"")
    print(f"{'='*70}\n")

    candidates = result.get("candidates", [])
    if not candidates:
        print(f"No candidates found. Error: {result.get('error', 'unknown')}")
        return

    header = f"{'#':<3} {'Part':<22} {'Mfr':<18} {'Tech':<12} {'V/I':<12} {'RthJC':<8} {'Tj_est':<8} {'$'}"
    print(header)
    print("-" * len(header))

    for i, c in enumerate(candidates, 1):
        p = c.get("params", {})
        t = c.get("thermal", {})
        tech = p.get("technology", "?")[:11]
        vi = f"{p.get('Vces', '?')}V/{p.get('Ic_25C', '?')}A"
        rth = p.get("RthJC", "?")
        tj = f"{t.get('Tj_estimated_C', '?')}°C"
        status = "OK" if t.get("thermal_ok") else "WARN"
        print(
            f"{i:<3} {c['part_number']:<22} "
            f"{c['manufacturer'][:17]:<18} "
            f"{tech:<12} "
            f"{vi:<12} "
            f"{rth:<8} "
            f"{tj:<8} "
            f"${c.get('price', 0):.2f}"
        )

    rec = result.get("recommendation", {})
    if rec:
        print(f"\n{'='*70}")
        print("RECOMMENDATION:")
        print(rec.get("text", ""))
        print(f"{'='*70}")


def cmd_heatsink(args):
    """Select heatsink for power devices."""
    sel = PowerComponentSelector()
    specs = {
        "p_loss": args.p_loss,
        "rth_jc": args.rth_jc,
        "rth_cs": args.rth_cs,
        "tj_max": args.tj_max,
        "t_ambient": args.t_ambient,
        "cooling": args.cooling,
    }

    result = sel.select_heatsink(specs)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    mock_tag = " [MOCK DATA]" if result.get("mock") else ""
    print(f"\n{'='*70}")
    print(f"Heatsink Selection{mock_tag}")
    print(f"  P_loss={args.p_loss}W, RthJC={args.rth_jc}°C/W, "
          f"Tj_max={args.tj_max}°C, Ta={args.t_ambient}°C")
    print(f"  Required Rth_SA ≤ {result.get('rth_sa_required', '?')} °C/W")
    print(f"{'='*70}\n")

    if result.get("error"):
        print(f"Error: {result['error']}")
        return

    candidates = result.get("candidates", [])
    if not candidates:
        print("No suitable heatsinks found.")
        return

    header = f"{'#':<3} {'Part':<28} {'Type':<15} {'Rth_SA':<10} {'Tj_est':<8} {'Margin':<8} {'$'}"
    print(header)
    print("-" * len(header))

    for i, c in enumerate(candidates, 1):
        p = c.get("params", {})
        t = c.get("thermal", {})
        hs_type = p.get("type", "?")[:14]
        ok = "OK" if t.get("adequate") else "INSUF"
        print(
            f"{i:<3} {c['part_number']:<28} "
            f"{hs_type:<15} "
            f"{t.get('Rth_SA', '?'):<10} "
            f"{t.get('Tj_estimated_C', '?')}°C{'':<3} "
            f"{t.get('margin_C', '?')}°C{'':<3} "
            f"${c.get('price', 0):.2f} [{ok}]"
        )


def cmd_bom(args):
    """Optimize BOM for cost and check inventory."""
    bom_opt = BOMOptimizer()

    # Build component list from JSON file or args
    if args.input:
        with open(args.input) as f:
            data = json.load(f)
        components = data.get("components", data) if isinstance(data, dict) else data
    else:
        # Simple inline BOM from repeated --part args
        components = []
        if args.parts:
            for part_spec in args.parts:
                parts = part_spec.split(":")
                pn = parts[0]
                qty = int(parts[1]) if len(parts) > 1 else 1
                desc = parts[2] if len(parts) > 2 else ""
                components.append({
                    "part_number": pn,
                    "digikey_pn": pn,
                    "quantity_per_unit": qty,
                    "description": desc,
                })

    if not components:
        print("Error: no components specified. Use --input BOM.json or --part PN:QTY")
        return

    result = bom_opt.optimize_bom(
        components, args.quantity, check_inventory=not args.no_stock_check)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    if args.csv:
        csv_out = bom_to_csv(result, args.csv if args.csv != "stdout" else None)
        if args.csv == "stdout":
            print(csv_out)
        else:
            print(f"BOM exported to {csv_out}")
        return

    # Pretty print
    print(f"\n{'='*70}")
    print(f"BOM Optimization — {args.quantity} units")
    print(f"{'='*70}\n")

    header = f"{'#':<3} {'Part':<22} {'Qty/u':<7} {'Total':<7} {'$/pc':<8} {'Line$':<10} {'Stock':<12} {'Status'}"
    print(header)
    print("-" * len(header))

    for i, c in enumerate(result.get("components", []), 1):
        stock_str = str(c.get("stock", "?"))
        status = c.get("stock_alert") or "OK"
        print(
            f"{i:<3} {c['part_number']:<22} "
            f"{c['qty_per_unit']:<7} "
            f"{c['total_qty']:<7} "
            f"${c.get('unit_price', 0) or 0:<7.2f} "
            f"${c.get('line_cost', 0):<9.2f} "
            f"{stock_str:<12} "
            f"{status}"
        )

    print(f"\nTotal BOM cost: ${result.get('total_bom_cost', 0):.2f}")
    print(f"Cost per unit:  ${result.get('cost_per_unit', 0):.2f}")

    alerts = result.get("stock_alerts", [])
    if alerts:
        print(f"\n⚠ INVENTORY ALERTS:")
        for a in alerts:
            print(f"  {a['part_number']}: {a['alert']}"
                  + (f" (shortage: {a['shortage']} pcs)" if a.get("shortage") else ""))


def cmd_gate_resistor(args):
    """Calculate gate resistors for a MOSFET."""
    calc = FOMCalculator()
    params = {
        "Qg": args.qg,
        "Ciss": args.ciss or 2000,
        "Vgs_th": args.vth or 4.0,
        "technology": "SiC" if args.sic else "Si",
    }
    result = calc.calculate_gate_resistors(
        params,
        driver_io_source=args.io_source,
        driver_io_sink=args.io_sink,
        fsw=args.fsw,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    print(f"\nGate Resistor Calculation:")
    print(f"  MOSFET: Qg={args.qg}nC, {'SiC' if args.sic else 'Si'}")
    print(f"  Driver: Io_source={args.io_source}A, Io_sink={args.io_sink}A")
    print(f"\n  Rg_on:  {result['Rg_on_ohm']}Ω → t_on={result['t_on_ns']:.0f}ns, P={result['P_rg_on_W']:.3f}W")
    print(f"  Rg_off: {result['Rg_off_ohm']}Ω → t_off={result['t_off_ns']:.0f}ns, P={result['P_rg_off_W']:.3f}W")
    print(f"  Vgs_on={result['Vgs_on']}V, Vgs_off={result['Vgs_off']}V")
    for n in result.get("notes", []):
        print(f"  Note: {n}")


def cmd_bootstrap(args):
    """Calculate bootstrap capacitor for high-side driver."""
    calc = FOMCalculator()
    params = {
        "Qg": args.qg,
        "technology": "SiC" if args.sic else "Si",
    }
    result = calc.bootstrap_capacitor(params, args.duty, args.fsw, args.vcc)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    print(f"\nBootstrap Capacitor Calculation:")
    print(f"  MOSFET: Qg={args.qg}nC, {'SiC' if args.sic else 'Si'}")
    print(f"  Duty_max={args.duty*100:.0f}%, Fsw={args.fsw/1e3:.0f}kHz, Vcc={args.vcc}V")
    print(f"\n  C_boot minimum:     {result['C_boot_min_uF']:.3f} µF")
    print(f"  C_boot recommended: {result['C_boot_recommended_uF']} µF ({result['dielectric']})")
    print(f"  Voltage rating:     ≥ {result['V_rating_min']}V")
    print(f"  Max ripple:         {result['V_ripple_max_V']}V")
    for n in result.get("notes", []):
        print(f"  Note: {n}")


def cmd_capacitor(args):
    """Search capacitors for PE applications."""
    sel = PowerComponentSelector()
    result = sel.select_capacitor({
        "cap_type": args.cap_type,
        "voltage": args.voltage,
        "budget": args.budget,
    })

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    mock_tag = " [MOCK DATA]" if result.get("mock") else ""
    print(f"\n{'='*70}")
    print(f"Capacitor Search: {args.cap_type.upper()}{mock_tag}")
    print(f"  Voltage: {args.voltage}V")
    print(f"{'='*70}\n")

    candidates = result.get("candidates", [])
    if not candidates:
        print("No candidates found.")
        return

    header = f"{'#':<3} {'Part':<25} {'Mfr':<18} {'Cap':<12} {'Voltage':<10} {'Type':<10} {'$'}"
    print(header)
    print("-" * len(header))
    for i, c in enumerate(candidates[:30], 1):
        p = c.get("params", {})
        print(
            f"{i:<3} {c['part_number'][:24]:<25} "
            f"{c['manufacturer'][:17]:<18} "
            f"{str(p.get('capacitance', '-'))[:11]:<12} "
            f"{str(p.get('voltage_rated_str', '-'))[:9]:<10} "
            f"{str(p.get('dielectric', '-'))[:9]:<10} "
            f"${c.get('price', 0):.2f}"
        )


def cmd_magnetics(args):
    """Search magnetic components."""
    sel = PowerComponentSelector()
    result = sel.select_magnetics({
        "mag_type": args.mag_type,
        "current": args.current,
        "budget": args.budget,
    })

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    mock_tag = " [MOCK DATA]" if result.get("mock") else ""
    print(f"\n{'='*70}")
    print(f"Magnetics Search: {args.mag_type.upper()}{mock_tag}")
    if args.mag_type not in ("ferrite_core", "nanocrystalline", "bobbin"):
        print(f"  Current: {args.current}A")
    print(f"{'='*70}\n")

    candidates = result.get("candidates", [])
    if not candidates:
        print("No candidates found.")
        return

    header = f"{'#':<3} {'Part':<25} {'Mfr':<18} {'Description':<40} {'$'}"
    print(header)
    print("-" * len(header))
    for i, c in enumerate(candidates[:30], 1):
        desc = c.get('description', '')[:39]
        print(
            f"{i:<3} {c['part_number'][:24]:<25} "
            f"{c['manufacturer'][:17]:<18} "
            f"{desc:<40} "
            f"${c.get('price', 0):.2f}"
        )


def cmd_xref(args):
    """Find substitute/alternative parts."""
    sel = PowerComponentSelector()
    result = sel.find_substitutes(args.part_number)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    if result.get("error"):
        print(f"Error: {result['error']}")
        return

    orig = result["original"]
    print(f"\n{'='*70}")
    print(f"Cross-Reference: {orig['part_number']} ({orig['manufacturer']})")
    print(f"  {orig['description']}")
    print(f"  Price: ${orig.get('price', 0):.2f}  Stock: {orig.get('stock', '?')}")
    print(f"{'='*70}\n")

    subs = result.get("substitutes", [])
    if not subs:
        print("No substitutes found.")
        return

    header = f"{'#':<3} {'Part':<25} {'Mfr':<18} {'Description':<35} {'$':<7} {'Stock':<8} {'Source'}"
    print(header)
    print("-" * len(header))
    for i, s in enumerate(subs[:20], 1):
        print(
            f"{i:<3} {s.get('part_number', '')[:24]:<25} "
            f"{s.get('manufacturer', '')[:17]:<18} "
            f"{s.get('description', '')[:34]:<35} "
            f"${s.get('price', 0) or 0:<6.2f} "
            f"{str(s.get('stock', '-'))[:7]:<8} "
            f"{s.get('source', '')}"
        )


def main():
    parser = argparse.ArgumentParser(
        description="Power Electronics Component Selection Tool"
    )
    sub = parser.add_subparsers(dest="command")

    # MOSFET selection
    mp = sub.add_parser("mosfet", help="Select MOSFETs for a converter")
    mp.add_argument("--topology", default="dab",
                    choices=["dab", "llc", "cllc", "buck", "boost", "full_bridge", "npc", "t_type", "pfc"])
    mp.add_argument("--vin", type=float, default=400, help="Input voltage [V]")
    mp.add_argument("--vout", type=float, default=96, help="Output voltage [V]")
    mp.add_argument("--power", type=float, default=5000, help="Output power [W]")
    mp.add_argument("--fsw", type=float, default=100e3, help="Switching frequency [Hz]")
    mp.add_argument("--zvs", action="store_true", default=True, help="ZVS operation")
    mp.add_argument("--budget", type=float, help="Max price per device [USD]")
    mp.add_argument("--n-devices", type=int, default=4, help="Number of MOSFETs")
    mp.add_argument("--json", action="store_true")
    mp.add_argument("--csv", nargs="?", const="stdout", help="Export to CSV (file path or stdout)")

    # Gate driver selection
    gp = sub.add_parser("gate-driver", help="Select gate drivers for a MOSFET")
    gp.add_argument("--qg", type=float, default=95, help="MOSFET Qg [nC]")
    gp.add_argument("--ciss", type=float, help="MOSFET Ciss [pF]")
    gp.add_argument("--coss", type=float, help="MOSFET Coss [pF]")
    gp.add_argument("--rds", type=float, default=25, help="MOSFET Rds(on) [mΩ]")
    gp.add_argument("--sic", action="store_true", help="SiC MOSFET")
    gp.add_argument("--topology", default="dab")
    gp.add_argument("--fsw", type=float, default=100e3, help="Fsw [Hz]")
    gp.add_argument("--json", action="store_true")

    # Datasheet parser
    pp = sub.add_parser("parse", help="Parse a datasheet PDF")
    pp.add_argument("pdf", help="Path to datasheet PDF")
    pp.add_argument("--json", action="store_true")

    # FOM calculator
    fp = sub.add_parser("fom", help="Calculate FOMs")
    fp.add_argument("--rds", type=float, required=True, help="Rds(on) [mΩ]")
    fp.add_argument("--qg", type=float, required=True, help="Qg [nC]")
    fp.add_argument("--qoss", type=float, help="Qoss [nC]")
    fp.add_argument("--coss", type=float, help="Coss [pF]")
    fp.add_argument("--price", type=float, help="Unit price [USD]")

    # Symbol fetcher
    sp = sub.add_parser("symbols", help="Fetch symbols/footprints")
    sp.add_argument("part_number", nargs="?", help="Part number")
    sp.add_argument("--manufacturer", default="", help="Manufacturer")
    sp.add_argument("--output", default="./symbols", help="Output directory")
    sp.add_argument("--kicad", action="store_true", help="KiCad 9 format")
    sp.add_argument("--altium", action="store_true", help="Altium format")
    sp.add_argument("--batch", help="BOM JSON file for batch download")

    # Power module selection
    pmp = sub.add_parser("power-module", help="Select power modules for high-power converters")
    pmp.add_argument("--topology", default="dab",
                    choices=["dab", "llc", "cllc", "buck", "boost", "full_bridge", "inverter", "npc", "t_type", "pfc"])
    pmp.add_argument("--vin", type=float, default=800, help="Input voltage [V]")
    pmp.add_argument("--vout", type=float, default=400, help="Output voltage [V]")
    pmp.add_argument("--power", type=float, default=100000, help="Output power [W]")
    pmp.add_argument("--fsw", type=float, default=20e3, help="Switching frequency [Hz]")
    pmp.add_argument("--cooling", default="air", choices=["air", "liquid"])
    pmp.add_argument("--budget", type=float, help="Max price per module [USD]")
    pmp.add_argument("--sic", action="store_true", help="Prefer SiC over IGBT")
    pmp.add_argument("--json", action="store_true")

    # Heatsink selection
    hp = sub.add_parser("heatsink", help="Select heatsink for power devices")
    hp.add_argument("--p-loss", type=float, default=100, help="Power dissipation [W]")
    hp.add_argument("--rth-jc", type=float, default=0.5, help="Junction-case thermal resistance [°C/W]")
    hp.add_argument("--rth-cs", type=float, default=0.1, help="Case-sink thermal resistance [°C/W]")
    hp.add_argument("--tj-max", type=float, default=175, help="Max junction temperature [°C]")
    hp.add_argument("--t-ambient", type=float, default=40, help="Ambient temperature [°C]")
    hp.add_argument("--cooling", default="forced_air", choices=["natural", "forced_air", "liquid"])
    hp.add_argument("--json", action="store_true")

    # BOM optimization
    bp = sub.add_parser("bom", help="Optimize BOM cost and check inventory")
    bp.add_argument("--input", help="BOM JSON file")
    bp.add_argument("--part", dest="parts", action="append",
                    help="Part spec as PN:QTY[:DESC] (repeatable)")
    bp.add_argument("--quantity", type=int, default=100, help="Production quantity [units]")
    bp.add_argument("--no-stock-check", action="store_true", help="Skip inventory check")
    bp.add_argument("--json", action="store_true")
    bp.add_argument("--csv", nargs="?", const="stdout",
                    help="Export to CSV (file path or stdout)")

    # Gate resistor calculation
    grp = sub.add_parser("gate-resistor", help="Calculate gate resistors")
    grp.add_argument("--qg", type=float, default=95, help="MOSFET Qg [nC]")
    grp.add_argument("--ciss", type=float, help="MOSFET Ciss [pF]")
    grp.add_argument("--vth", type=float, help="Gate threshold voltage [V]")
    grp.add_argument("--sic", action="store_true", help="SiC MOSFET")
    grp.add_argument("--io-source", type=float, default=4.0, help="Driver source current [A]")
    grp.add_argument("--io-sink", type=float, default=6.0, help="Driver sink current [A]")
    grp.add_argument("--fsw", type=float, default=100e3, help="Switching frequency [Hz]")
    grp.add_argument("--json", action="store_true")

    # Bootstrap capacitor calculation
    bsp = sub.add_parser("bootstrap", help="Calculate bootstrap capacitor")
    bsp.add_argument("--qg", type=float, default=95, help="MOSFET Qg [nC]")
    bsp.add_argument("--sic", action="store_true", help="SiC MOSFET")
    bsp.add_argument("--duty", type=float, default=0.95, help="Max duty cycle")
    bsp.add_argument("--fsw", type=float, default=100e3, help="Switching frequency [Hz]")
    bsp.add_argument("--vcc", type=float, default=15.0, help="Driver supply voltage [V]")
    bsp.add_argument("--json", action="store_true")

    # Capacitor selection
    cp = sub.add_parser("capacitor", help="Search capacitors for PE applications")
    cp.add_argument("--type", dest="cap_type", default="dc_link",
                    choices=["dc_link", "resonant", "filter", "emi_x", "emi_y",
                             "snubber", "bootstrap"])
    cp.add_argument("--voltage", type=float, default=400, help="Voltage rating [V]")
    cp.add_argument("--budget", type=float, help="Max price [USD]")
    cp.add_argument("--json", action="store_true")

    # Magnetics selection
    mg = sub.add_parser("magnetics", help="Search magnetic components")
    mg.add_argument("--type", dest="mag_type", default="ferrite_core",
                    choices=["ferrite_core", "nanocrystalline", "inductor",
                             "pfc_inductor", "transformer", "bobbin", "cmc",
                             "emi_filter"])
    mg.add_argument("--current", type=float, default=10, help="Current rating [A]")
    mg.add_argument("--budget", type=float, help="Max price [USD]")
    mg.add_argument("--json", action="store_true")

    # Cross-reference / substitution
    xr = sub.add_parser("xref", help="Find substitute/alternative parts")
    xr.add_argument("part_number", help="Part number to find alternatives for")
    xr.add_argument("--json", action="store_true")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cmds = {
        "mosfet": cmd_mosfet,
        "gate-driver": cmd_gate_driver,
        "parse": cmd_parse,
        "fom": cmd_fom,
        "symbols": cmd_symbols,
        "power-module": cmd_power_module,
        "heatsink": cmd_heatsink,
        "bom": cmd_bom,
        "gate-resistor": cmd_gate_resistor,
        "bootstrap": cmd_bootstrap,
        "capacitor": cmd_capacitor,
        "magnetics": cmd_magnetics,
        "xref": cmd_xref,
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
