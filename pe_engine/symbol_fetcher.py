"""Download schematic symbols and footprints for KiCad 9 and Altium Designer.

Backend priority:
1. Nexar GraphQL API (official, supports KiCad + Altium, needs free API key)
2. LCSC/EasyEDA via easyeda2kicad (KiCad only, no key needed)
3. SnapMagic/SnapEDA API (if API key available)
4. Fallback: manual download links for Ultra Librarian, CSE, SnapEDA
"""
import json
import logging
import os
import subprocess
import shutil
import zipfile
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# Nexar GraphQL queries
NEXAR_TOKEN_URL = "https://identity.nexar.com/connect/token"
NEXAR_GRAPHQL_URL = "https://api.nexar.com/graphql/"

NEXAR_SEARCH_QUERY = """
query SearchParts($q: String!) {
  supSearch(q: $q, limit: 3) {
    results {
      part {
        mpn
        manufacturer { name }
        descriptions { text }
        bestDatasheet { url }
        cadModels {
          provider
          downloadUrl
          format
        }
      }
    }
  }
}
"""


class SymbolFetcher:
    """Fetch schematic symbols and PCB footprints for electronic components."""

    FORMATS = {
        "kicad9": {
            "name": "KiCad 9",
            "extensions": [".kicad_sym", ".kicad_mod", ".step"],
        },
        "altium": {
            "name": "Altium Designer",
            "extensions": [".SchLib", ".PcbLib", ".step"],
        },
    }

    def __init__(self, output_dir: str = "./symbols",
                 nexar_client_id: str | None = None,
                 nexar_client_secret: str | None = None,
                 snapmagic_api_key: str | None = None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.nexar_id = nexar_client_id or os.getenv("NEXAR_CLIENT_ID", "")
        self.nexar_secret = nexar_client_secret or os.getenv("NEXAR_CLIENT_SECRET", "")
        self.snapmagic_key = snapmagic_api_key or os.getenv("SNAPMAGIC_API_KEY", "")

        self._nexar_token: str | None = None

    # --- Public API ---

    def fetch(self, part_number: str, manufacturer: str = "",
              formats: list | None = None) -> dict:
        """Fetch symbol and footprint for a component.

        Args:
            part_number: Manufacturer part number (e.g., "C3M0025065K")
            manufacturer: Manufacturer name (helps disambiguation)
            formats: List of format keys (default: ["kicad9", "altium"])

        Returns:
            Dict with download results per format.
        """
        if formats is None:
            formats = ["kicad9", "altium"]

        results = {"part_number": part_number, "formats": {}}

        for fmt in formats:
            if fmt not in self.FORMATS:
                results["formats"][fmt] = {"error": f"Unknown format: {fmt}"}
                continue
            result = self._try_fetch(part_number, manufacturer, fmt)
            results["formats"][fmt] = result

        return results

    def fetch_batch(self, bom: list, formats: list | None = None) -> dict:
        """Fetch symbols for an entire BOM.

        Args:
            bom: List of dicts with 'part_number' and optional 'manufacturer'
            formats: Target formats (default: ["kicad9", "altium"])

        Returns:
            Dict with results per component and summary stats.
        """
        if formats is None:
            formats = ["kicad9", "altium"]

        results = []
        success_count = 0
        manual_count = 0

        for item in bom:
            pn = item.get("part_number", item.get("ManufacturerPartNumber", ""))
            mfr = item.get("manufacturer", item.get("Manufacturer", ""))
            if not pn:
                continue

            result = self.fetch(pn, mfr, formats)
            results.append(result)

            any_success = any(
                r.get("success") for r in result.get("formats", {}).values()
            )
            if any_success:
                success_count += 1
            else:
                manual_count += 1

        return {
            "total": len(results),
            "auto_downloaded": success_count,
            "manual_required": manual_count,
            "results": results,
            "output_directory": str(self.output_dir),
        }

    def generate_manual_download_report(self, batch_result: dict) -> str:
        """Generate a report of parts needing manual symbol download."""
        lines = ["# Symbol/Footprint Download Report", ""]

        auto = [r for r in batch_result["results"]
                if any(f.get("success") for f in r.get("formats", {}).values())]
        manual = [r for r in batch_result["results"]
                  if not any(f.get("success") for f in r.get("formats", {}).values())]

        if auto:
            lines.append(f"## Auto-downloaded ({len(auto)} parts)")
            for r in auto:
                pn = r["part_number"]
                for fmt, info in r.get("formats", {}).items():
                    if info.get("success"):
                        lines.append(f"  - {pn} [{fmt}]: {info.get('source', '?')}")
            lines.append("")

        if manual:
            lines.append(f"## Manual download required ({len(manual)} parts)")
            lines.append("")
            for r in manual:
                pn = r["part_number"]
                lines.append(f"### {pn}")
                for fmt, info in r.get("formats", {}).items():
                    links = info.get("manual_links", {})
                    for name, url in links.items():
                        lines.append(f"  - [{name}]({url})")
                lines.append("")

        return "\n".join(lines)

    # --- Backend dispatch ---

    def _try_fetch(self, part_number: str, manufacturer: str, fmt: str) -> dict:
        """Try backends in priority order."""

        # Backend 1: Nexar GraphQL (official API, best for both formats)
        if self.nexar_id and self.nexar_secret:
            result = self._fetch_nexar(part_number, fmt)
            if result.get("success"):
                return result

        # Backend 2: easyeda2kicad (KiCad only, no API key needed)
        if fmt == "kicad9":
            result = self._fetch_easyeda(part_number)
            if result.get("success"):
                return result

        # Backend 3: SnapMagic API (if key available)
        if self.snapmagic_key:
            result = self._fetch_snapmagic(part_number, fmt)
            if result.get("success"):
                return result

        # Fallback: manual download links
        return {
            "success": False,
            "part_number": part_number,
            "format": fmt,
            "manual_links": {
                "Ultra Librarian": f"https://app.ultralibrarian.com/search?q={part_number}",
                "SnapEDA": f"https://www.snapeda.com/search/?q={part_number}",
                "Component Search Engine": f"https://componentsearchengine.com/search?term={part_number}",
                "LCSC": f"https://www.lcsc.com/search?q={part_number}",
            },
            "instructions": (
                f"Automatic download not available for {part_number} ({fmt}). "
                "Visit the links above to download manually."
            ),
        }

    # --- Backend 1: Nexar GraphQL ---

    def _nexar_authenticate(self) -> str | None:
        """Get Nexar access token via OAuth2 client credentials."""
        if self._nexar_token:
            return self._nexar_token

        try:
            resp = requests.post(
                NEXAR_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.nexar_id,
                    "client_secret": self.nexar_secret,
                },
                timeout=30,
            )
            resp.raise_for_status()
            self._nexar_token = resp.json()["access_token"]
            logger.info("Nexar token acquired")
            return self._nexar_token
        except Exception as e:
            logger.warning("Nexar auth failed: %s", e)
            return None

    def _fetch_nexar(self, part_number: str, fmt: str) -> dict:
        """Fetch CAD models via Nexar GraphQL API."""
        token = self._nexar_authenticate()
        if not token:
            return {"success": False, "reason": "Nexar authentication failed"}

        try:
            resp = requests.post(
                NEXAR_GRAPHQL_URL,
                json={"query": NEXAR_SEARCH_QUERY, "variables": {"q": part_number}},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()

            results = data.get("data", {}).get("supSearch", {}).get("results", [])
            if not results:
                return {"success": False, "reason": "No results on Nexar"}

            part = results[0].get("part", {})
            cad_models = part.get("cadModels", [])

            if not cad_models:
                return {"success": False, "reason": "No CAD models available"}

            # Find matching format
            fmt_info = self.FORMATS[fmt]
            target_exts = fmt_info["extensions"]
            downloaded_files = []
            save_dir = self.output_dir / part_number / fmt
            save_dir.mkdir(parents=True, exist_ok=True)

            for model in cad_models:
                dl_url = model.get("downloadUrl")
                model_fmt = model.get("format", "")
                if dl_url:
                    fname = f"{part_number}_{model_fmt}"
                    fpath = save_dir / fname
                    try:
                        self._download_file(dl_url, str(fpath))
                        downloaded_files.append(fname)
                    except Exception as e:
                        logger.warning("Download failed for %s: %s", dl_url, e)

            if downloaded_files:
                return {
                    "success": True,
                    "source": "Nexar",
                    "format": fmt,
                    "files": downloaded_files,
                    "directory": str(save_dir),
                    "mpn": part.get("mpn"),
                    "manufacturer": part.get("manufacturer", {}).get("name", ""),
                }

            return {"success": False, "reason": "No downloadable CAD models matched format"}
        except Exception as e:
            return {"success": False, "reason": f"Nexar error: {e}"}

    # --- Backend 2: easyeda2kicad (LCSC/EasyEDA) ---

    @staticmethod
    def _mpn_to_lcsc_id(mpn: str) -> str | None:
        """Look up LCSC ID from manufacturer part number via JLCPCB API."""
        try:
            resp = requests.post(
                "https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart"
                "/smtGood/selectSmtComponentList",
                json={"keyword": mpn, "pageSize": 5, "currentPage": 1},
                headers={"Content-Type": "application/json",
                         "User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            products = (data.get("data", {})
                        .get("componentPageInfo", {})
                        .get("list", []))
            # Find exact MPN match
            for p in products:
                if p.get("componentModelEn", "").upper() == mpn.upper():
                    return p.get("componentCode")
            # Fall back to first result
            if products:
                return products[0].get("componentCode")
        except Exception as e:
            logger.warning("LCSC ID lookup failed for %s: %s", mpn, e)
        return None

    def _fetch_easyeda(self, part_number: str) -> dict:
        """Fetch KiCad symbols via easyeda2kicad + JLCPCB/LCSC lookup.

        Flow: MPN → JLCPCB API → LCSC ID → easyeda2kicad → KiCad files.
        No API key required. KiCad format only.
        """
        # Check if easyeda2kicad is installed
        if not shutil.which("easyeda2kicad"):
            try:
                result = subprocess.run(
                    ["python3", "-m", "easyeda2kicad", "--help"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode != 0:
                    return {
                        "success": False,
                        "reason": "easyeda2kicad not installed. Run: pip install easyeda2kicad",
                    }
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return {
                    "success": False,
                    "reason": "easyeda2kicad not installed. Run: pip install easyeda2kicad",
                }

        # Resolve LCSC ID: accept either LCSC ID (C...) or MPN
        if part_number.startswith("C") and part_number[1:].isdigit():
            lcsc_id = part_number
        else:
            lcsc_id = self._mpn_to_lcsc_id(part_number)
            if not lcsc_id:
                return {
                    "success": False,
                    "reason": f"Part {part_number} not found on LCSC/JLCPCB",
                }
            logger.info("Resolved %s → LCSC %s", part_number, lcsc_id)

        save_dir = self.output_dir / part_number / "kicad9"
        save_dir.mkdir(parents=True, exist_ok=True)
        output_base = str(save_dir / part_number)

        try:
            cmd = [
                "python3", "-m", "easyeda2kicad",
                "--symbol", "--footprint",
                "--lcsc_id", lcsc_id,
                "--output", output_base,
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
            )

            # Collect generated files
            files = []
            sym_file = Path(f"{output_base}.kicad_sym")
            fp_dir = Path(f"{output_base}.pretty")
            if sym_file.exists():
                files.append(sym_file.name)
            if fp_dir.exists():
                files.extend(f.name for f in fp_dir.iterdir() if f.is_file())

            if files:
                return {
                    "success": True,
                    "source": "EasyEDA/LCSC",
                    "lcsc_id": lcsc_id,
                    "format": "kicad9",
                    "files": files,
                    "directory": str(save_dir),
                }

            return {
                "success": False,
                "reason": f"easyeda2kicad produced no files: {result.stderr[:200]}",
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "reason": "easyeda2kicad timed out"}
        except Exception as e:
            return {"success": False, "reason": f"easyeda2kicad error: {e}"}

    # --- Backend 3: SnapMagic ---

    def _fetch_snapmagic(self, part_number: str, fmt: str) -> dict:
        """Fetch from SnapMagic/SnapEDA API."""
        try:
            resp = requests.get(
                "https://www.snapeda.com/api/v1/parts/search",
                params={"q": part_number, "limit": 1},
                headers={"Authorization": f"Bearer {self.snapmagic_key}"},
                timeout=30,
            )
            if resp.status_code != 200:
                return {"success": False, "reason": f"SnapMagic API: {resp.status_code}"}

            data = resp.json()
            parts = data.get("results", [])
            if not parts:
                return {"success": False, "reason": "No results on SnapMagic"}

            part = parts[0]
            dl_url = part.get("download_url")
            if not dl_url:
                return {"success": False, "reason": "No download URL from SnapMagic"}

            save_dir = self.output_dir / part_number / fmt
            save_dir.mkdir(parents=True, exist_ok=True)
            zip_path = save_dir / f"{part_number}.zip"
            self._download_file(dl_url, str(zip_path))
            files = self._extract_zip(str(zip_path), str(save_dir))

            return {
                "success": True,
                "source": "SnapMagic",
                "format": fmt,
                "files": files,
                "directory": str(save_dir),
            }
        except Exception as e:
            return {"success": False, "reason": f"SnapMagic error: {e}"}

    # --- Utilities ---

    def _download_file(self, url: str, save_path: str):
        """Download a file from URL."""
        resp = requests.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info("Downloaded: %s", save_path)

    def _extract_zip(self, zip_path: str, extract_to: str) -> list:
        """Extract a ZIP file and return list of extracted files."""
        files = []
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_to)
                files = zf.namelist()
            os.remove(zip_path)
        except zipfile.BadZipFile:
            logger.warning("Not a valid ZIP: %s", zip_path)
            files = [os.path.basename(zip_path)]
        return files
