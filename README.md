# DigiKey Skill PE — Power Electronics Component Selection

A [Claude Code](https://claude.com/claude-code) skill for intelligent power electronics component selection using the DigiKey API.

## Features

- **MOSFET selection** — FOM ranking (Rds×Qg, Rds×Qoss), multi-vendor coverage, voltage derating
- **Gate driver matching** — parametric search based on MOSFET Qg/Ciss, vendor-diversified
- **Gate resistor / bootstrap cap** — Rg_on/Rg_off (E24 values), Cboot sizing
- **Capacitor search** — DC-link, resonant, filter, EMI (X/Y), snubber
- **Magnetic components** — ferrite cores, nanocrystalline, inductors, PFC, transformers, bobbins, CMC, EMI filters
- **Cross-reference** — find alternatives for discontinued or hard-to-source parts
- **Power modules / heatsinks** — high-power converter selection
- **BOM optimization** — pricing tiers, stock alerts, CSV export
- **Datasheet parsing** — extract parameters from PDF (ST, Infineon, ROHM, TI, Wolfspeed)
- **KiCad footprint download** — automatic via easyeda2kicad (free, no API key)

## Install

```bash
git clone https://github.com/HZou9/digikey-skill-pe.git
cd digikey-skill-pe
pip install -r requirements.txt
pip install easyeda2kicad  # optional, for KiCad footprints
```

Register in Claude Code:
```
/install-skill /path/to/digikey-skill-pe
```

## DigiKey API Setup

1. Register at https://developer.digikey.com
2. Create an app → get **Client ID** + **Client Secret**
3. Copy `.env.template` to `.env` and fill in credentials

```
DIGIKEY_CLIENT_ID=your_id
DIGIKEY_CLIENT_SECRET=your_secret
DIGIKEY_USE_SANDBOX=false
DIGIKEY_MOCK_MODE=auto
```

No credentials? The skill runs in **mock mode** automatically — full functionality with sample data.

## Usage Examples

```bash
# MOSFET selection for 5kW DAB converter
python scripts/pe_select.py mosfet --topology dab --vin 400 --vout 400 --power 5000 --fsw 100000

# Gate driver for SiC MOSFET (Qg=79nC)
python scripts/pe_select.py gate-driver --qg 79 --sic --topology dab

# DC-link capacitors 400V 10uF
python scripts/pe_select.py capacitor --type dc_link --voltage 400 --capacitance 10uF

# Ferrite cores / nanocrystalline / CMC
python scripts/pe_select.py magnetics --type ferrite_core
python scripts/pe_select.py magnetics --type nanocrystalline
python scripts/pe_select.py magnetics --type cmc --current 15

# Find alternatives for a discontinued part
python scripts/pe_select.py xref C3M0025065K

# KiCad symbol + footprint download
python scripts/pe_select.py symbols UCC21530DWKR --kicad

# BOM cost analysis
python scripts/pe_select.py bom --part C3M0025065K-ND:4 --quantity 100 --csv bom.csv
```

## Architecture

```
digikey-skill-pe/
├── SKILL.md              # Claude Code skill definition
├── digikey_api/          # DigiKey API client (OAuth2 + mock + cache)
├── pe_engine/
│   ├── selector.py       # Component search (MOSFET/driver/cap/magnetics/xref)
│   ├── fom.py            # FOM calculator + gate resistor + bootstrap
│   ├── datasheet_parser.py  # PDF parameter extraction
│   ├── bom.py            # BOM optimizer + CSV export
│   └── symbol_fetcher.py # KiCad footprint download (easyeda2kicad)
├── scripts/pe_select.py  # CLI entry point (13 commands)
└── tests/                # 76 unit tests
```

## License

MIT
