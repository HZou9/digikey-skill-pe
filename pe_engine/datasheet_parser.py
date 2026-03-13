"""PDF datasheet parser for semiconductor components.

Extracts electrical parameters from MOSFET, gate driver, and capacitor
datasheets using pdfplumber table extraction — no LLM required.
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    pdfplumber = None


@dataclass
class ParsedParam:
    """A single extracted parameter."""
    name: str
    symbol: str
    value: float | None
    unit: str
    min_val: float | None = None
    typ_val: float | None = None
    max_val: float | None = None
    conditions: str = ""
    confidence: str = "high"  # high, medium, low


@dataclass
class DatasheetResult:
    """Complete parsed datasheet."""
    component_type: str = "unknown"  # mosfet, gate_driver, capacitor
    manufacturer: str = ""
    part_number: str = ""
    package: str = ""
    params: dict = field(default_factory=dict)
    raw_tables: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def get(self, key: str, default=None):
        p = self.params.get(key)
        if p is None:
            return default
        # Return typ if available, else max, else min
        if p.typ_val is not None:
            return p.typ_val
        if p.max_val is not None:
            return p.max_val
        if p.min_val is not None:
            return p.min_val
        return p.value if p.value is not None else default

    def summary(self) -> dict:
        """Return a flat dict of key-value pairs for FOM calculation."""
        out = {"component_type": self.component_type,
               "manufacturer": self.manufacturer,
               "part_number": self.part_number,
               "package": self.package}
        for key, p in self.params.items():
            out[key] = self.get(key)
            out[f"{key}_min"] = p.min_val
            out[f"{key}_max"] = p.max_val
            out[f"{key}_unit"] = p.unit
            out[f"{key}_conditions"] = p.conditions
        return out


# --- Regex patterns for parameter matching ---

MOSFET_PATTERNS = {
    "Vds_max": [
        r"[Dd]rain[\s\-]*[Ss]ource\s+[Vv]oltage.*?(\d+\.?\d*)\s*V",
        r"V[_\s]?DSS\s+(\d+)\s*V",
        r"VDSS\s+(\d+)\s*V",
    ],
    "Vgs_max": [
        r"V[_\s]?[Gg][Ss][Ss]\b.*?[±]?\s*(\d+\.?\d*)\s*V",
        r"Gate[\s-]*Source\s+Voltage.*?[±]?\s*(\d+\.?\d*)\s*V",
    ],
    "Id_max": [
        r"[Cc]ontinuous\s+[Dd]rain\s+[Cc]urrent.*?(\d+\.?\d*)\s*A",
        r"I[_\s]?D\s*(?:@|,|=|\s)\s*T\s*[=<]\s*25.*?(\d+\.?\d*)\s*A",
    ],
    "Rds_on": [
        r"R[_\s]?[Dd][Ss]\s*\(?[Oo][Nn]\)?\s*.*?(\d+\.?\d*)\s*(m?Ω|mohm)",
        r"[Oo]n[\s-]*[Rr]esistance.*?(\d+\.?\d*)\s*(m?Ω|mohm)",
    ],
    "Vgs_th": [
        r"V[_\s]?[Gg][Ss]\s*\(?th\)?\s*.*?(\d+\.?\d*)\s*V",
        r"[Tt]hreshold\s+[Vv]oltage.*?(\d+\.?\d*)\s*V",
    ],
    "Qg": [
        r"Q[_\s]?[Gg]\b(?!\s*[ds]).*?(\d+\.?\d*)\s*(n?C)",
        r"[Tt]otal\s+[Gg]ate\s+[Cc]harge.*?(\d+\.?\d*)\s*(n?C)",
    ],
    "Qgs": [r"Q[_\s]?[Gg][Ss]\b.*?(\d+\.?\d*)\s*(n?C)"],
    "Qgd": [r"Q[_\s]?[Gg][Dd]\b.*?(\d+\.?\d*)\s*(n?C)"],
    "Qoss": [r"Q[_\s]?[Oo][Ss][Ss]\b.*?(\d+\.?\d*)\s*(n?C)"],
    "Ciss": [
        r"C[_\s]?[Ii][Ss][Ss]\b.*?(\d+\.?\d*)\s*(p?F|nF)",
        r"[Ii]nput\s+[Cc]apacitance.*?(\d+\.?\d*)\s*(p?F|nF)",
    ],
    "Coss": [
        r"C[_\s]?[Oo][Ss][Ss]\b.*?(\d+\.?\d*)\s*(p?F|nF)",
        r"[Oo]utput\s+[Cc]apacitance.*?(\d+\.?\d*)\s*(p?F|nF)",
    ],
    "Crss": [
        r"C[_\s]?[Rr][Ss][Ss]\b.*?(\d+\.?\d*)\s*(p?F|nF)",
        r"[Rr]everse\s+[Tt]ransfer\s+[Cc]apacitance.*?(\d+\.?\d*)\s*(p?F|nF)",
    ],
    "Eoss": [r"E[_\s]?[Oo][Ss][Ss]\b.*?(\d+\.?\d*)\s*(µ?J|uJ|mJ)"],
    "Eon": [r"E[_\s]?[Oo][Nn]\b.*?(\d+\.?\d*)\s*(µ?J|uJ|mJ)"],
    "Eoff": [r"E[_\s]?[Oo][Ff][Ff]\b.*?(\d+\.?\d*)\s*(µ?J|uJ|mJ)"],
    "Vsd": [r"V[_\s]?[Ss][Dd]\b.*?(\d+\.?\d*)\s*V"],
    "trr": [r"t[_\s]?[Rr][Rr]\b.*?(\d+\.?\d*)\s*(ns|µs)"],
    "Qrr": [r"Q[_\s]?[Rr][Rr]\b.*?(\d+\.?\d*)\s*(n?C|µC)"],
    "RthJC": [
        r"R[_\s]?θ?[_\s]?[Jj][Cc]\b.*?(\d+\.?\d*)\s*(°C/W|K/W)",
        r"[Jj]unction[\s-]*[Cc]ase.*?(\d+\.?\d*)\s*(°C/W|K/W)",
    ],
    "RthJA": [
        r"R[_\s]?θ?[_\s]?[Jj][Aa]\b.*?(\d+\.?\d*)\s*(°C/W|K/W)",
        r"[Jj]unction[\s-]*[Aa]mbient.*?(\d+\.?\d*)\s*(°C/W|K/W)",
    ],
    "Pd_max": [
        r"P[_\s]?[Dd]\b.*?(\d+\.?\d*)\s*W",
        r"[Pp]ower\s+[Dd]issipation.*?(\d+\.?\d*)\s*W",
    ],
}

GATE_DRIVER_PATTERNS = {
    "tpd_rise": [
        r"[Tt][\s_]?[Pp][Dd]\s*\(?[Rr]ise\)?.*?(\d+\.?\d*)\s*(ns|µs)",
        r"[Pp]ropagation\s+[Dd]elay.*?[Rr]is.*?(\d+\.?\d*)\s*(ns|µs)",
    ],
    "tpd_fall": [
        r"[Tt][\s_]?[Pp][Dd]\s*\(?[Ff]all\)?.*?(\d+\.?\d*)\s*(ns|µs)",
        r"[Pp]ropagation\s+[Dd]elay.*?[Ff]all.*?(\d+\.?\d*)\s*(ns|µs)",
    ],
    "tpd_skew": [
        r"[Ss]kew.*?(\d+\.?\d*)\s*(ns)",
        r"[Pp]ropagation\s+[Dd]elay\s+[Ss]kew.*?(\d+\.?\d*)\s*(ns)",
    ],
    "Io_source": [
        r"[Ss]ource\s+[Cc]urrent.*?(\d+\.?\d*)\s*A",
        r"[Pp]eak\s+[Oo]utput.*?[Ss]ource.*?(\d+\.?\d*)\s*A",
    ],
    "Io_sink": [
        r"[Ss]ink\s+[Cc]urrent.*?(\d+\.?\d*)\s*A",
        r"[Pp]eak\s+[Oo]utput.*?[Ss]ink.*?(\d+\.?\d*)\s*A",
    ],
    "tr": [r"[Rr]ise\s+[Tt]ime.*?(\d+\.?\d*)\s*(ns|µs)"],
    "tf": [r"[Ff]all\s+[Tt]ime.*?(\d+\.?\d*)\s*(ns|µs)"],
    "Vcc_min": [r"V[_\s]?CC[I]?\s+[Ii]nput\s+[Ss]upply.*?[Mm]in.*?(\d+\.?\d*)\s*V",
                 r"V[_\s]?CC[I]?\s+[Rr]ange.*?(\d+\.?\d*)\s*V"],
    "Vcc_max": [r"V[_\s]?CC[I]?\s+[Ii]nput\s+[Ss]upply.*?[Mm]ax.*?(\d+\.?\d*)\s*V",
                 r"V[_\s]?CC[I]?\s+[Rr]ange.*?to\s+(\d+\.?\d*)\s*V"],
    "CMTI": [r"CMTI.*?(\d+\.?\d*)\s*(V/ns|kV/µs)"],
    "isolation_voltage": [
        r"[Ii]solation\s+[Vv]oltage.*?(\d+\.?\d*)\s*(V|kV)",
        r"V[_\s]?[Ii][Oo][Rr][Mm].*?(\d+\.?\d*)\s*(V|kV)",
    ],
}

# Unit normalization: convert everything to base units
UNIT_CONVERSIONS = {
    "mΩ": ("mOhm", 1.0), "mohm": ("mOhm", 1.0), "Ω": ("mOhm", 1000.0),
    "nC": ("nC", 1.0), "µC": ("nC", 1000.0), "pC": ("nC", 0.001),
    "pF": ("pF", 1.0), "nF": ("pF", 1000.0), "µF": ("pF", 1e6),
    "ns": ("ns", 1.0), "µs": ("ns", 1000.0), "us": ("ns", 1000.0),
    "µJ": ("uJ", 1.0), "uJ": ("uJ", 1.0), "mJ": ("uJ", 1000.0), "J": ("uJ", 1e6),
    "V": ("V", 1.0), "kV": ("V", 1000.0),
    "A": ("A", 1.0), "mA": ("A", 0.001),
    "W": ("W", 1.0), "mW": ("W", 0.001), "kW": ("W", 1000.0),
    "°C/W": ("C/W", 1.0), "K/W": ("C/W", 1.0),
    "V/ns": ("V/ns", 1.0), "kV/µs": ("V/ns", 1.0),
}

PACKAGE_PATTERNS = [
    r"TO-247[-\s]?\d*", r"TO-263[-\s]?\d*", r"D2?PAK", r"DPAK",
    r"TO-220[-\s]?\d*", r"SOT-\d+", r"SO-?\d+", r"TSSOP-?\d+",
    r"QFN[-\s]?\d+", r"SOIC-?\d+", r"\d{4}\s*\(\d{4}\s*[Mm]etric\)",
]


def _normalize_unit(value: float, unit: str) -> tuple[float, str]:
    """Normalize a value to base unit."""
    if unit in UNIT_CONVERSIONS:
        base_unit, factor = UNIT_CONVERSIONS[unit]
        return value * factor, base_unit
    return value, unit


# Default units for common parameters (used when table has no Unit column)
DEFAULT_UNITS = {
    "Vds_max": "V", "Vgs_max": "V", "Vgs_th": "V", "Vsd": "V",
    "Vcc_min": "V", "Vcc_max": "V",
    "Id_max": "A", "Pd_max": "W",
    "Rds_on": "mOhm",
    "Qg": "nC", "Qgs": "nC", "Qgd": "nC", "Qoss": "nC", "Qrr": "nC",
    "Ciss": "pF", "Coss": "pF", "Crss": "pF",
    "Eoss": "uJ", "Eon": "uJ", "Eoff": "uJ",
    "trr": "ns",
    "RthJC": "C/W", "RthJA": "C/W",
    "isolation_voltage": "V",
    "CMTI": "V/ns",
    "Io_source": "A", "Io_sink": "A",
    "tpd_rise": "ns", "tpd_fall": "ns", "tr": "ns", "tf": "ns",
}


def _parse_number(text: str) -> float | None:
    """Extract a number from text."""
    m = re.search(r"[-+]?\d+\.?\d*(?:[eE][-+]?\d+)?", text.strip())
    if m:
        return float(m.group())
    return None


class DatasheetParser:
    """Parse semiconductor datasheets to extract electrical parameters."""

    def parse(self, pdf_path: str) -> DatasheetResult:
        """Parse a datasheet PDF and return structured parameters."""
        if pdfplumber is None:
            raise ImportError("pdfplumber is required: pip install pdfplumber")

        result = DatasheetResult()
        path = Path(pdf_path)
        if not path.exists():
            result.warnings.append(f"File not found: {pdf_path}")
            return result

        with pdfplumber.open(pdf_path) as pdf:
            # Extract text and tables from first 10 pages
            all_text = ""
            all_tables = []
            for page in pdf.pages[:10]:
                text = page.extract_text() or ""
                all_text += text + "\n"
                tables = page.extract_tables()
                for t in tables:
                    if t and len(t) > 1:
                        all_tables.append(t)

            result.raw_tables = all_tables

            # Detect component type
            result.component_type = self._detect_type(all_text)

            # Extract part number and manufacturer from first page
            result.part_number = self._extract_part_number(all_text)
            result.manufacturer = self._extract_manufacturer(all_text)
            result.package = self._extract_package(all_text)

            # Parse parameters based on type
            if result.component_type == "mosfet":
                self._extract_params(all_text, all_tables, MOSFET_PATTERNS, result)
            elif result.component_type == "gate_driver":
                self._extract_params(all_text, all_tables, GATE_DRIVER_PATTERNS, result)
            else:
                # Try both
                self._extract_params(all_text, all_tables, MOSFET_PATTERNS, result)
                self._extract_params(all_text, all_tables, GATE_DRIVER_PATTERNS, result)

            # Fill in default units for params that have no unit
            for key, p in result.params.items():
                if not p.unit and key in DEFAULT_UNITS:
                    p.unit = DEFAULT_UNITS[key]

        return result

    def _detect_type(self, text: str) -> str:
        """Detect component type from datasheet text."""
        tl = text.lower()
        mosfet_score = sum(1 for kw in [
            "mosfet", "rds(on)", "drain-source", "gate charge", "vdss",
            "n-channel", "p-channel", "sic mosfet", "power transistor",
        ] if kw in tl)
        driver_score = sum(1 for kw in [
            "gate driver", "half bridge", "half-bridge", "bootstrap",
            "propagation delay", "dead time", "dead-time", "uvlo",
            "isolated driver", "high-side", "low-side",
        ] if kw in tl)
        cap_score = sum(1 for kw in [
            "capacitor", "mlcc", "capacitance", "esr", "ripple current",
            "dielectric", "x7r", "x5r", "c0g", "np0",
        ] if kw in tl)

        scores = {"mosfet": mosfet_score, "gate_driver": driver_score, "capacitor": cap_score}
        best = max(scores, key=scores.get)
        return best if scores[best] > 0 else "unknown"

    def _extract_part_number(self, text: str) -> str:
        """Extract part number from first lines."""
        lines = text.split("\n")[:10]
        for line in lines:
            # Typical patterns: alphanumeric with hyphens, 6+ chars
            m = re.search(r"\b([A-Z][A-Z0-9]{2,}[-]?[A-Z0-9]{2,}[A-Z0-9]*)\b", line)
            if m and len(m.group(1)) >= 6:
                return m.group(1)
        return ""

    def _extract_manufacturer(self, text: str) -> str:
        """Detect manufacturer from text.

        Uses first-page text (first ~40 lines) for priority detection,
        then falls back to full text. This prevents false matches from
        competitor part references deep in the document.
        """
        # Ordered by specificity: longer/more-specific names first to avoid
        # "cree" matching before "texas instruments" on a TI datasheet.
        known = [
            ("texas instruments", "Texas Instruments"),
            ("analog devices", "Analog Devices"),
            ("on semiconductor", "onsemi"),
            ("stmicroelectronics", "STMicroelectronics"),
            ("silicon labs", "Silicon Labs"),
            ("infineon", "Infineon"),
            ("wolfspeed", "Wolfspeed"),
            ("cree", "Wolfspeed"),
            ("onsemi", "onsemi"),
            ("rohm", "ROHM"),
            ("vishay", "Vishay"),
            ("nexperia", "Nexperia"),
            ("toshiba", "Toshiba"),
            ("microchip", "Microchip"),
            ("renesas", "Renesas"),
            ("skyworks", "Skyworks"),
        ]
        # Check first page (first ~40 lines) with priority
        first_page = "\n".join(text.split("\n")[:40]).lower()
        for key, name in known:
            if key in first_page:
                return name
        # Fallback to full text
        tl = text.lower()
        for key, name in known:
            if key in tl:
                return name
        return ""

    def _extract_package(self, text: str) -> str:
        """Extract package type."""
        for pat in PACKAGE_PATTERNS:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                return m.group().strip()
        return ""

    def _extract_params(self, text: str, tables: list, patterns: dict,
                        result: DatasheetResult):
        """Extract parameters using regex patterns on text and tables."""
        # Normalize text: collapse newline-split symbols like "V\nDSS" → "VDSS"
        text_norm = re.sub(r'(\w)\n(\w)', r'\1\2', text)

        # First pass: search in raw text (catches inline specs)
        for param_name, regexes in patterns.items():
            if param_name in result.params:
                continue
            for regex in regexes:
                m = re.search(regex, text_norm, re.IGNORECASE)
                if m:
                    groups = m.groups()
                    val = _parse_number(groups[0])
                    unit = groups[1] if len(groups) > 1 else ""
                    if val is not None:
                        norm_val, norm_unit = _normalize_unit(val, unit)
                        result.params[param_name] = ParsedParam(
                            name=param_name, symbol=param_name,
                            value=norm_val, unit=norm_unit,
                            typ_val=norm_val,
                            conditions=self._extract_conditions(m.group()),
                            confidence="medium",
                        )
                        break

        # Second pass: search in table cells (more structured, higher confidence)
        for table in tables:
            self._parse_table(table, patterns, result)
            # Also try parsing "Symbol + Value" tables (absolute max ratings)
            self._parse_simple_table(table, patterns, result)

    def _parse_table(self, table: list, patterns: dict, result: DatasheetResult):
        """Parse a single table for parameters."""
        if not table or len(table) < 2:
            return

        # Try to identify column roles from header row(s)
        # Some datasheets (e.g. ROHM) use multi-row headers where
        # row 0 = ['Symbol', 'Conditions', 'Values', None, None]
        # row 1 = [None, None, 'Min.', 'Typ.', 'Max.']
        header = table[0]
        if header is None:
            return

        col_map = self._identify_columns(header)
        data_start = 1

        # If header didn't have min/typ/max, check row 1 for sub-header
        if not col_map or not any(k in col_map for k in ["min", "typ", "max"]):
            if len(table) > 2:
                merged = self._merge_header_rows(header, table[1])
                col_map = self._identify_columns(merged)
                if col_map:
                    data_start = 2

        if not col_map:
            return

        # Parse each data row
        prev_cells = [None] * len(header)
        for row in table[data_start:]:
            if row is None:
                continue
            # Fill None cells from previous row (merged cells)
            cells = []
            for i, cell in enumerate(row):
                if cell is None and i < len(prev_cells):
                    cells.append(prev_cells[i])
                else:
                    cells.append(cell)
            prev_cells = cells

            # Get parameter cell text and normalize newlines in symbols
            param_text = ""
            if "param" in col_map and col_map["param"] < len(cells):
                param_text = str(cells[col_map["param"]] or "")
            if "symbol" in col_map and col_map["symbol"] < len(cells):
                sym_text = str(cells[col_map["symbol"]] or "")
                param_text += " " + sym_text

            # Normalize newline-separated symbols: "V\nDSS" → "VDSS",
            # "R\nDS(on)" → "RDS(on)", "C\niss" → "Ciss", "Q\nrr" → "Qrr"
            # Also strip footnote markers like "*5", "*1" from symbols
            param_text_norm = re.sub(r'\s*\*\d+\s*', '', param_text)  # strip *5, *1, etc.
            param_text_norm = re.sub(r'(\w)\n(\w)', r'\1\2', param_text_norm)
            # Also collapse any remaining newlines
            param_text_norm = param_text_norm.replace('\n', ' ')

            if not param_text_norm.strip():
                continue

            # Match against known patterns
            for param_name, regexes in patterns.items():
                # Check if this row mentions the parameter
                matched = False
                for regex in regexes:
                    # Simplified: just check if the parameter symbol is in the text
                    if re.search(regex.split(r".*?")[0], param_text_norm, re.IGNORECASE):
                        matched = True
                        break

                if not matched:
                    # Also try direct symbol matching
                    symbol_map = {
                        "Rds_on": ["rds(on)", "rdson", "r_ds(on)"],
                        "Qg": ["qg", "q_g"],
                        "Qgs": ["qgs", "q_gs"],
                        "Qgd": ["qgd", "q_gd"],
                        "Qoss": ["qoss", "q_oss"],
                        "Ciss": ["ciss", "c_iss"],
                        "Coss": ["coss", "c_oss"],
                        "Crss": ["crss", "c_rss"],
                        "Eoss": ["eoss", "e_oss"],
                        "Eon": ["eon", "e_on"],
                        "Eoff": ["eoff", "e_off"],
                        "Vds_max": ["vdss", "v_dss", "v(br)dss", "v (br)dss", "vds"],
                        "Vgs_max": ["vgss", "v_gss", "vgs(max)"],
                        "Id_max": ["continuous drain current", "id"],
                        "Vgs_th": ["vgs(th)", "v_gs(th)", "vgs (th)"],
                        "Pd_max": ["pd", "p_d"],
                        "RthJC": ["rthjc", "rθjc", "r_θjc"],
                        "RthJA": ["rthja", "rθja", "r_θja"],
                        "Vsd": ["vsd", "v_sd"],
                        "trr": ["trr", "t_rr"],
                        "Qrr": ["qrr", "q_rr"],
                        # Gate driver symbols
                        "Io_source": ["ioa+", "iob+", "peak output source",
                                      "source current", "io+"],
                        "Io_sink": ["ioa-", "iob-", "peak output sink",
                                    "sink current", "io-"],
                        "tpd_rise": ["tpdlh", "t_pdlh", "propagation delay from inx to outx rising",
                                     "propagation delay.*rising"],
                        "tpd_fall": ["tpdhl", "t_pdhl", "propagation delay from inx to outx falling",
                                     "propagation delay.*falling"],
                        "tr": ["trise", "t_rise", "output rise time"],
                        "tf": ["tfall", "t_fall", "output fall time"],
                    }
                    if param_name in symbol_map:
                        pt_lower = param_text_norm.lower()
                        for sym in symbol_map[param_name]:
                            # Support regex patterns in symbol entries
                            if '.*' in sym or '\\b' in sym:
                                if re.search(sym, pt_lower):
                                    matched = True
                                    break
                            else:
                                # Use word-boundary matching to prevent
                                # "qg" matching in "qgd", "id" in "idss"
                                escaped = re.escape(sym)
                                if re.search(r'(?<![a-z])' + escaped + r'(?![a-z(])',
                                             pt_lower):
                                    matched = True
                                    break

                if matched:
                    # Skip if this param already has a high-confidence value
                    # (prevents merged-cell rows from overwriting, e.g. ID at
                    # Tc=25°C being overwritten by ID at Tc=100°C)
                    if param_name in result.params and result.params[param_name].confidence == "high":
                        continue

                    # Extract min/typ/max values from row
                    vals = self._extract_row_values(cells, col_map)
                    unit = ""
                    if "unit" in col_map and col_map["unit"] < len(cells):
                        unit = str(cells[col_map["unit"]] or "").strip()
                    conditions = ""
                    if "conditions" in col_map and col_map["conditions"] < len(cells):
                        conditions = str(cells[col_map["conditions"]] or "").strip()
                        # Clean up newlines in conditions too
                        conditions = conditions.replace('\n', ' ')

                    best_val = vals.get("typ") or vals.get("max") or vals.get("min")
                    if best_val is not None:
                        norm_val, norm_unit = _normalize_unit(best_val, unit)
                        norm_min = None
                        norm_max = None
                        norm_typ = None
                        if vals.get("min") is not None:
                            norm_min, _ = _normalize_unit(vals["min"], unit)
                        if vals.get("typ") is not None:
                            norm_typ, _ = _normalize_unit(vals["typ"], unit)
                        if vals.get("max") is not None:
                            norm_max, _ = _normalize_unit(vals["max"], unit)

                        result.params[param_name] = ParsedParam(
                            name=param_name, symbol=param_name,
                            value=norm_val, unit=norm_unit,
                            min_val=norm_min, typ_val=norm_typ, max_val=norm_max,
                            conditions=conditions,
                            confidence="high",
                        )

    def _parse_simple_table(self, table: list, patterns: dict, result: DatasheetResult):
        """Parse simple 'Symbol + Value' tables (absolute maximum ratings).

        These tables have no Min/Typ/Max columns. Format examples:
          ['Parameter', 'Symbol', 'Value', 'Unit']  (Wolfspeed)
          [None, 'Symbol', 'Value']  (ROHM)
        """
        if not table or len(table) < 2:
            return

        header = table[0]
        if header is None:
            return

        # Find symbol and value columns
        sym_col = val_col = unit_col = param_col = None
        for i, cell in enumerate(header):
            if cell is None:
                continue
            cl = str(cell).lower().strip()
            if cl in ("symbol", "sym", "sym."):
                sym_col = i
            elif cl in ("value", "values", "rating"):
                val_col = i
            elif cl in ("unit", "units"):
                unit_col = i
            elif any(k in cl for k in ["parameter", "description"]):
                param_col = i

        if sym_col is None or val_col is None:
            return

        # Only process if this table does NOT have min/typ/max (handled elsewhere)
        has_minmax = any(str(c or "").lower().strip() in
                         ("min", "min.", "typ", "typ.", "max", "max.")
                         for c in header)
        if has_minmax:
            return

        # Symbol map for absolute maximum ratings
        abs_max_symbols = {
            "Vds_max": ["vdss", "v_dss", "v(br)dss", "v (br)dss", "vds"],
            "Id_max": ["id", "i_d"],
            "Vgs_max": ["vgss", "v_gss", "vgs(max)"],
            "Pd_max": ["pd", "p_d"],
        }

        for row in table[1:]:
            if row is None:
                continue
            if sym_col >= len(row):
                continue

            sym_text = str(row[sym_col] or "")
            # Also include parameter column text
            full_text = sym_text
            if param_col is not None and param_col < len(row):
                full_text = str(row[param_col] or "") + " " + sym_text

            # Strip footnotes and normalize newlines
            full_text = re.sub(r'\s*\*\d+\s*', '', full_text)
            full_text = re.sub(r'(\w)\n(\w)', r'\1\2', full_text)
            full_text = full_text.replace('\n', ' ')

            if not full_text.strip():
                continue

            ft_lower = full_text.lower().strip()

            for param_name, symbols in abs_max_symbols.items():
                if param_name not in patterns:
                    continue
                # Don't override high-confidence table values
                if param_name in result.params and result.params[param_name].confidence == "high":
                    continue

                matched_sym = False
                for sym in symbols:
                    escaped = re.escape(sym)
                    if re.search(r'(?<![a-z])' + escaped + r'(?![a-z(])', ft_lower):
                        matched_sym = True
                        break

                if matched_sym and val_col < len(row):
                    val_text = str(row[val_col] or "").split('\n')[0].strip()
                    val = _parse_number(val_text)
                    if val is not None:
                        unit = ""
                        if unit_col is not None and unit_col < len(row):
                            unit = str(row[unit_col] or "").strip()
                        norm_val, norm_unit = _normalize_unit(val, unit)
                        result.params[param_name] = ParsedParam(
                            name=param_name, symbol=param_name,
                            value=norm_val, unit=norm_unit,
                            max_val=norm_val,  # Absolute max ratings
                            conditions="Absolute Maximum",
                            confidence="high",
                        )

    def _merge_header_rows(self, row1: list, row2: list) -> list:
        """Merge two header rows into one (for multi-row table headers).

        Example:
            row1 = ['Symbol', 'Conditions', 'Values', None, None]
            row2 = [None, None, 'Min.', 'Typ.', 'Max.']
            result = ['Symbol', 'Conditions', 'Min.', 'Typ.', 'Max.']
        """
        merged = []
        max_len = max(len(row1), len(row2))
        for i in range(max_len):
            c1 = row1[i] if i < len(row1) else None
            c2 = row2[i] if i < len(row2) else None
            # Prefer sub-header (row2) if non-None, else use row1
            if c2 is not None:
                merged.append(c2)
            elif c1 is not None:
                merged.append(c1)
            else:
                merged.append(None)
        return merged

    def _identify_columns(self, header: list) -> dict:
        """Identify column roles from header row."""
        col_map = {}
        for i, cell in enumerate(header):
            if cell is None:
                continue
            cl = str(cell).lower().strip()
            if any(k in cl for k in ["parameter", "description"]):
                col_map["param"] = i
            elif cl in ("symbol", "sym", "sym."):
                col_map["symbol"] = i
            elif any(k in cl for k in ["condition", "test condition"]):
                col_map["conditions"] = i
            elif cl in ("min", "min."):
                col_map["min"] = i
            elif cl in ("typ", "typ.", "typical"):
                col_map["typ"] = i
            elif cl in ("max", "max.", "maximum"):
                col_map["max"] = i
            elif cl in ("unit", "units"):
                col_map["unit"] = i

        # Need at least parameter or symbol column, plus one value column
        if "param" not in col_map and "symbol" not in col_map:
            return {}
        if not any(k in col_map for k in ["min", "typ", "max"]):
            return {}

        # If we have "symbol" but not "param", treat symbol as the param column too
        if "symbol" in col_map and "param" not in col_map:
            col_map["param"] = col_map["symbol"]

        return col_map

    def _extract_row_values(self, cells: list, col_map: dict) -> dict:
        """Extract min/typ/max from a table row.

        Handles:
        - Standard columns: separate min/typ/max cells
        - Multi-value cells: "22\\n32" → take first value (ROHM format)
        - Space-separated values: "26 33 45" in min column when typ/max
          columns are empty (TI format: min typ max crammed together)
        """
        vals = {}
        for key in ["min", "typ", "max"]:
            if key in col_map and col_map[key] < len(cells):
                cell = cells[col_map[key]]
                if cell is not None:
                    cell_str = str(cell).strip()
                    if not cell_str or cell_str == '-':
                        continue
                    # Handle newline-separated values: take first line
                    first_line = cell_str.split('\n')[0].strip()
                    if first_line and first_line != '-':
                        v = _parse_number(first_line)
                        if v is not None:
                            vals[key] = v

        # TI format: if only min has a value but it contains multiple
        # space-separated numbers, interpret as min typ max
        if "min" in vals and "typ" not in vals and "max" not in vals:
            min_col = col_map.get("min")
            if min_col is not None and min_col < len(cells):
                cell_str = str(cells[min_col] or "").split('\n')[0].strip()
                nums = re.findall(r'[-+]?\d+\.?\d*', cell_str)
                if len(nums) == 3:
                    vals["min"] = float(nums[0])
                    vals["typ"] = float(nums[1])
                    vals["max"] = float(nums[2])
                elif len(nums) == 2:
                    # Could be min/max or typ/max
                    vals["min"] = float(nums[0])
                    vals["max"] = float(nums[1])

        return vals

    def _extract_conditions(self, text: str) -> str:
        """Extract test conditions from parameter text."""
        cond_patterns = [
            r"(?:V[Gg][Ss]\s*=\s*\d+\.?\d*\s*V)",
            r"(?:V[Dd][Ss]\s*=\s*\d+\.?\d*\s*V)",
            r"(?:I[Dd]\s*=\s*\d+\.?\d*\s*A)",
            r"(?:T[Jj]\s*=\s*\d+\.?\d*\s*°C)",
            r"(?:f\s*=\s*\d+\.?\d*\s*[kM]?Hz)",
        ]
        found = []
        for pat in cond_patterns:
            m = re.search(pat, text)
            if m:
                found.append(m.group())
        return ", ".join(found)


def parse_datasheet(pdf_path: str) -> DatasheetResult:
    """Convenience function to parse a datasheet."""
    parser = DatasheetParser()
    return parser.parse(pdf_path)


def parse_from_digikey_params(parameters: list) -> dict:
    """Parse parameters from DigiKey API response (no PDF needed).

    Args:
        parameters: List of dicts with "Name" and "Value" keys
                    from DigiKey product details.

    Returns:
        Dict of extracted specs suitable for FOM calculation.
    """
    specs = {}
    for p in parameters:
        name = p.get("Name", "")
        value = p.get("Value", "")

        if "Rds On" in name or "RDS(on)" in name:
            m = re.search(r"(\d+\.?\d*)\s*(m?Ω|mohm)", value, re.IGNORECASE)
            if m:
                v, u = float(m.group(1)), m.group(2)
                specs["Rds_on"] = v if "m" in u.lower() else v * 1000

        elif "Gate Charge" in name and "Qg" in name:
            m = re.search(r"(\d+\.?\d*)\s*(n?C)", value)
            if m:
                specs["Qg"] = float(m.group(1))

        elif "Drain-Source Voltage" in name or "Vdss" in name:
            m = re.search(r"(\d+\.?\d*)\s*V", value)
            if m:
                specs["Vds_max"] = float(m.group(1))

        elif "Current" in name and "Drain" in name and "25" in name:
            m = re.search(r"(\d+\.?\d*)\s*A", value)
            if m:
                specs["Id_max"] = float(m.group(1))

        elif "Input Capacitance" in name or "Ciss" in name:
            m = re.search(r"(\d+\.?\d*)\s*(p?F|nF)", value)
            if m:
                v, u = float(m.group(1)), m.group(2)
                specs["Ciss"] = v if "p" in u.lower() else v * 1000

        elif "Power Dissipation" in name:
            m = re.search(r"(\d+\.?\d*)\s*W", value)
            if m:
                specs["Pd_max"] = float(m.group(1))

        elif "Package" in name or "Case" in name:
            specs["package"] = value

        elif "Temperature" in name and "Operating" in name:
            specs["temp_range"] = value

        elif "Technology" in name:
            specs["technology"] = value

        elif "FET Type" in name:
            specs["fet_type"] = value

        # Gate driver specific
        elif "Peak Output Current" in name:
            m = re.findall(r"(\d+\.?\d*)\s*A", value)
            if len(m) >= 2:
                specs["Io_source"] = float(m[0])
                specs["Io_sink"] = float(m[1])
            elif m:
                specs["Io_source"] = float(m[0])

        elif "Propagation Delay" in name:
            m = re.search(r"(\d+\.?\d*)\s*(ns|µs)", value)
            if m:
                specs["tpd"] = float(m.group(1))

        elif "CMTI" in name:
            m = re.search(r"(\d+\.?\d*)\s*(V/ns|kV/µs)", value)
            if m:
                specs["CMTI"] = float(m.group(1))

        elif "Isolation" in name and "Voltage" in name:
            m = re.search(r"(\d+\.?\d*)\s*(V|kV)", value)
            if m:
                v, u = float(m.group(1)), m.group(2)
                specs["isolation_voltage"] = v if u == "V" else v * 1000

    return specs
