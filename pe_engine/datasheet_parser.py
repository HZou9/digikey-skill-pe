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
        r"V[_\s]?[Dd][Ss][Ss]?\b.*?(\d+\.?\d*)\s*V",
        r"Drain[\s-]*Source\s+Voltage.*?(\d+\.?\d*)\s*V",
    ],
    "Vgs_max": [
        r"V[_\s]?[Gg][Ss][Ss]?\b.*?[±]?\s*(\d+\.?\d*)\s*V",
        r"Gate[\s-]*Source\s+Voltage.*?[±]?\s*(\d+\.?\d*)\s*V",
    ],
    "Id_max": [
        r"I[_\s]?[Dd]\b.*?(\d+\.?\d*)\s*A",
        r"Continuous\s+Drain\s+Current.*?(\d+\.?\d*)\s*A",
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
    "Vcc_min": [r"V[_\s]?[Cc][Cc].*?[Mm]in.*?(\d+\.?\d*)\s*V"],
    "Vcc_max": [r"V[_\s]?[Cc][Cc].*?[Mm]ax.*?(\d+\.?\d*)\s*V"],
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
            # Extract text and tables from first 6 pages
            all_text = ""
            all_tables = []
            for page in pdf.pages[:6]:
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
        """Detect manufacturer from text."""
        known = {
            "infineon": "Infineon", "wolfspeed": "Wolfspeed", "cree": "Wolfspeed",
            "onsemi": "onsemi", "on semiconductor": "onsemi",
            "stmicroelectronics": "STMicroelectronics", "rohm": "ROHM",
            "texas instruments": "Texas Instruments", "analog devices": "Analog Devices",
            "vishay": "Vishay", "nexperia": "Nexperia", "toshiba": "Toshiba",
            "microchip": "Microchip", "renesas": "Renesas",
            "skyworks": "Skyworks", "silicon labs": "Silicon Labs",
        }
        tl = text.lower()
        for key, name in known.items():
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
        # First pass: search in raw text (catches inline specs)
        for param_name, regexes in patterns.items():
            if param_name in result.params:
                continue
            for regex in regexes:
                m = re.search(regex, text, re.IGNORECASE)
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

    def _parse_table(self, table: list, patterns: dict, result: DatasheetResult):
        """Parse a single table for parameters."""
        if not table or len(table) < 2:
            return

        # Try to identify column roles from header row
        header = table[0]
        if header is None:
            return

        col_map = self._identify_columns(header)
        if not col_map:
            return

        # Parse each row
        prev_cells = [None] * len(header)
        for row in table[1:]:
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

            # Get parameter cell text
            param_text = ""
            if "param" in col_map and col_map["param"] < len(cells):
                param_text = str(cells[col_map["param"]] or "")
            if "symbol" in col_map and col_map["symbol"] < len(cells):
                param_text += " " + str(cells[col_map["symbol"]] or "")

            if not param_text.strip():
                continue

            # Match against known patterns
            for param_name, regexes in patterns.items():
                # Check if this row mentions the parameter
                matched = False
                for regex in regexes:
                    # Simplified: just check if the parameter symbol is in the text
                    if re.search(regex.split(r".*?")[0], param_text, re.IGNORECASE):
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
                        "Vds_max": ["vdss", "v_dss", "vds"],
                        "Vgs_max": ["vgss", "v_gss", "vgs"],
                        "Id_max": ["id", "i_d"],
                        "Vgs_th": ["vgs(th)", "v_gs(th)"],
                        "Pd_max": ["pd", "p_d"],
                        "RthJC": ["rthjc", "rθjc", "r_θjc"],
                        "RthJA": ["rthja", "rθja", "r_θja"],
                        "Vsd": ["vsd", "v_sd"],
                        "trr": ["trr", "t_rr"],
                        "Qrr": ["qrr", "q_rr"],
                    }
                    if param_name in symbol_map:
                        pt_lower = param_text.lower()
                        for sym in symbol_map[param_name]:
                            if sym in pt_lower:
                                matched = True
                                break

                if matched:
                    # Extract min/typ/max values from row
                    vals = self._extract_row_values(cells, col_map)
                    unit = ""
                    if "unit" in col_map and col_map["unit"] < len(cells):
                        unit = str(cells[col_map["unit"]] or "").strip()
                    conditions = ""
                    if "conditions" in col_map and col_map["conditions"] < len(cells):
                        conditions = str(cells[col_map["conditions"]] or "").strip()

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

        # Need at least parameter column and one value column
        if "param" not in col_map and "symbol" not in col_map:
            return {}
        if not any(k in col_map for k in ["min", "typ", "max"]):
            return {}
        return col_map

    def _extract_row_values(self, cells: list, col_map: dict) -> dict:
        """Extract min/typ/max from a table row."""
        vals = {}
        for key in ["min", "typ", "max"]:
            if key in col_map and col_map[key] < len(cells):
                cell = cells[col_map[key]]
                if cell is not None:
                    v = _parse_number(str(cell))
                    if v is not None:
                        vals[key] = v
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
