---
name: pe-select
description: Intelligent power electronics component selection — MOSFETs, gate drivers, capacitors, magnetic components, power modules, BOM optimization, KiCad footprint download, and cross-reference. Supports DAB, LLC, CLLC, buck, boost, NPC, PFC, and more.
disable-model-invocation: true
argument-hint: [mosfet|gate-driver|capacitor|magnetics|xref|symbols|bom|parse|fom|gate-resistor|bootstrap] [options]
allowed-tools: Bash(python *), Read, Grep
---

## Power Electronics Component Selection Skill

You are an expert power electronics design engineer with deep knowledge of semiconductor device physics, converter topologies, and component selection methodologies.

### Available Commands

Run from the `digikey-skill-pe/` directory:

```bash
# MOSFET selection for a DAB converter
python scripts/pe_select.py mosfet --topology dab --vin 400 --vout 96 --power 5000 --fsw 100000 --zvs

# MOSFET selection for NPC three-level converter
python scripts/pe_select.py mosfet --topology npc --vin 800 --vout 400 --power 10000 --fsw 50000

# MOSFET selection for PFC stage
python scripts/pe_select.py mosfet --topology pfc --vin 400 --vout 800 --power 3000 --fsw 65000

# Export MOSFET selection to CSV
python scripts/pe_select.py mosfet --topology dab --vin 400 --vout 96 --power 5000 --csv mosfets.csv

# Gate driver selection based on MOSFET parameters
python scripts/pe_select.py gate-driver --qg 95 --rds 25 --sic --topology dab --fsw 100000

# Gate resistor calculation (Rg_on/Rg_off with E24 values)
python scripts/pe_select.py gate-resistor --qg 95 --sic --io-source 4 --io-sink 6 --fsw 100000

# Bootstrap capacitor calculation for high-side driver
python scripts/pe_select.py bootstrap --qg 95 --duty 0.95 --fsw 100000 --vcc 15

# Power module selection for high-power converters (100kW+)
python scripts/pe_select.py power-module --topology dab --vin 800 --vout 400 --power 200000 --fsw 20000 --cooling liquid --sic

# Heatsink selection based on thermal requirements
python scripts/pe_select.py heatsink --p-loss 100 --rth-jc 0.46 --tj-max 175 --cooling forced_air

# BOM optimization with inventory check
python scripts/pe_select.py bom --part C3M0025065K-ND:4 --part 296-UCC21530QDWRQ1-ND:4 --quantity 100

# BOM from JSON file, export to CSV
python scripts/pe_select.py bom --input bom.json --quantity 100 --csv bom_output.csv

# Parse a MOSFET datasheet PDF
python scripts/pe_select.py parse /path/to/datasheet.pdf

# Calculate FOMs directly
python scripts/pe_select.py fom --rds 25 --qg 95 --qoss 42 --coss 500 --price 8.50

# Fetch KiCad symbols/footprints (auto: MPN → LCSC → easyeda2kicad)
python scripts/pe_select.py symbols UCC21530DWKR --kicad

# Capacitor search: 400V DC-link film caps
python scripts/pe_select.py capacitor --type dc_link --voltage 400 --capacitance 10uF

# Capacitor search: EMI X2 safety caps
python scripts/pe_select.py capacitor --type emi_x

# Magnetics: ferrite cores
python scripts/pe_select.py magnetics --type ferrite_core

# Magnetics: nanocrystalline toroidal cores
python scripts/pe_select.py magnetics --type nanocrystalline

# Magnetics: common mode chokes 15A
python scripts/pe_select.py magnetics --type cmc --current 15

# Magnetics: PFC inductors, EMI filters, transformers, bobbins
python scripts/pe_select.py magnetics --type pfc_inductor --current 20
python scripts/pe_select.py magnetics --type emi_filter --current 10
python scripts/pe_select.py magnetics --type transformer
python scripts/pe_select.py magnetics --type bobbin

# Cross-reference: find alternatives for a part
python scripts/pe_select.py xref C3M0025065K
```

### How to Handle User Queries

1. **"I need MOSFETs for a 5kW DAB converter"**
   → Run `mosfet` command with topology=dab and user's specs
   → Present FOM table with Rds×Qg and Rds×Qoss rankings
   → Show loss estimates and voltage derating check
   → Give clear recommendation with rationale

2. **"What gate driver for C3M0025065K?"**
   → Look up MOSFET params (Qg=95nC, SiC)
   → Run `gate-driver` with those params
   → Follow up with `gate-resistor` for Rg_on/Rg_off values
   → Show compatibility scores and dead-time recommendations

3. **"What gate resistor values for SiC MOSFET?"**
   → Run `gate-resistor` with MOSFET Qg, driver Io, and SiC flag
   → Show Rg_on/Rg_off (E24 standard values), switching times
   → Note SiC needs separate Rg_on/Rg_off with diode steering

4. **"Compare these MOSFETs for ZVS application"**
   → Run `fom` for each device
   → Emphasize Rds×Qoss FOM (critical for soft-switching)
   → Explain topology-specific FOM weights

5. **"Parse this datasheet"**
   → Run `parse` command
   → Show extracted parameters with confidence levels
   → Calculate FOMs from parsed data

6. **"I need a power module for a 200kW inverter"**
   → Run `power-module` command with topology, voltage, power specs
   → Show thermal analysis (Tj estimate, cooling requirement)
   → Compare SiC module vs IGBT module trade-offs
   → Note: for >200kW, recommend paralleling or custom modules

7. **"What heatsink for this MOSFET at 100W loss?"**
   → Run `heatsink` command with P_loss, RthJC, cooling type
   → Show required Rth_SA and matching heatsinks
   → Include thermal margin analysis

8. **"Generate BOM for this design"**
   → Run `bom` command with component list and quantity
   → Show pricing, stock status, and inventory alerts
   → Export to CSV with `--csv` flag

9. **"What bootstrap cap for buck converter?"**
   → Run `bootstrap` with MOSFET Qg, max duty, and Fsw
   → Show minimum and recommended capacitor value
   → Include voltage rating and dielectric recommendations

10. **"I need DC-link caps for a 400V/5kW converter"**
    → Run `capacitor --type dc_link --voltage 400 --capacitance 10uF`
    → Results filtered: only caps with voltage rating ≥ user requirement
    → Show capacitance, voltage, dielectric, price from multiple vendors

11. **"Find ferrite cores / nanocrystalline cores / CMC / EMI filter"**
    → Run `magnetics --type ferrite_core` or `nanocrystalline` or `cmc --current 15`
    → Covers: ferrite (E/PQ/RM/ETD/toroidal), nanocrystalline, amorphous
    → Also: inductors, PFC inductors, transformers, bobbins, CMC, EMI filters

12. **"This part is discontinued, find alternatives"**
    → Run `xref <part_number>`
    → Uses DigiKey substitutions API + parametric search
    → Auto-detects component type, builds targeted search
    → Vendor-diversified results

### FOM Knowledge Base

**Topology-specific FOM priorities:**
| Topology | Primary FOM | Secondary | Rationale |
|----------|------------|-----------|-----------|
| DAB/LLC/CLLC | Rds×Qoss | Rds×Qg | ZVS depends on output charge |
| Buck     | Rds×Qg     | Rds×Qoss  | Hard-switched, gate loss dominant |
| Boost/PFC | Rds×Qg    | Rds×Qoss  | Hard-switched (CCM) or mixed |
| Full Bridge | Rds×Qoss | Rds×Qg   | Phase-shift ZVS typical |
| NPC/T-type | Rds×Qoss  | Rds×Qg   | Soft-switching possible, clamped voltage |

**Loss estimation formulas:**
- P_cond = I²_rms × Rds(on)
- P_sw = Eoss × Fsw (soft-switched) or ½×Qg×Vgs×Fsw (hard-switched)
- P_gate = Qg × Vgs × Fsw

**Voltage derating:**
- Si MOSFET: 80% derating (use 500V part for 400V app)
- SiC MOSFET: 80% derating standard
- Ceramic cap: 50% derating (capacitance drops with DC bias)

### Important Notes:
- FOM ranking is a **reference only** — actual loss calculation depends on user's specific topology, modulation, and operating conditions
- Capacitor results are **filtered by minimum voltage** — caps below user's voltage requirement are excluded
- Gate driver results are **vendor-diversified** — no single vendor dominates the list
- SiC turn-off options: -5V, -2V, or 0V with active Miller clamp — all valid
- Symbol/footprint download uses easyeda2kicad (free, no API key needed)
