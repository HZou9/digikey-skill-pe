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
from pe_engine.bom import BOMOptimizer
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

    # Loss estimates
    if candidates[0].get("losses"):
        print(f"\nLoss Estimates (@ Fsw={args.fsw/1e3:.0f}kHz):")
        for i, c in enumerate(candidates[:3], 1):
            l = c.get("losses")
            if l:
                print(
                    f"  {i}. {c['part_number']}: "
                    f"P_cond={l.P_cond:.1f}W, P_sw={l.P_sw:.1f}W, "
                    f"P_gate={l.P_gate:.2f}W → Total={l.P_total:.1f}W"
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


def main():
    parser = argparse.ArgumentParser(
        description="Power Electronics Component Selection Tool"
    )
    sub = parser.add_subparsers(dest="command")

    # MOSFET selection
    mp = sub.add_parser("mosfet", help="Select MOSFETs for a converter")
    mp.add_argument("--topology", default="dab", choices=["dab", "llc", "buck", "boost", "full_bridge"])
    mp.add_argument("--vin", type=float, default=400, help="Input voltage [V]")
    mp.add_argument("--vout", type=float, default=96, help="Output voltage [V]")
    mp.add_argument("--power", type=float, default=5000, help="Output power [W]")
    mp.add_argument("--fsw", type=float, default=100e3, help="Switching frequency [Hz]")
    mp.add_argument("--zvs", action="store_true", default=True, help="ZVS operation")
    mp.add_argument("--budget", type=float, help="Max price per device [USD]")
    mp.add_argument("--n-devices", type=int, default=4, help="Number of MOSFETs")
    mp.add_argument("--json", action="store_true")

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
    }
    cmds[args.command](args)


if __name__ == "__main__":
    main()
