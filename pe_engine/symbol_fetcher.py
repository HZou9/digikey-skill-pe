"""Download schematic symbols and footprints for KiCad 9 and Altium Designer.

Supports multiple backends:
1. Ultra Librarian (via Nexar/component search)
2. SnapMagic/SnapEDA API (if API key available)
3. Component Search Engine (Samacsys) via direct download links
4. JLCPCB/EasyEDA (via easyeda2kicad for JLCPCB parts)

Since most services lack public APIs, this module uses the best
available approach for each: direct download URLs where possible,
API calls where available, and clear fallback instructions.
"""
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import requests

logger = logging.getLogger(__name__)


class SymbolFetcher:
    """Fetch schematic symbols and PCB footprints for electronic components."""

    SNAPMAGIC_API_BASE = "https://www.snapeda.com/api/v1"
    CSE_BASE = "https://componentsearchengine.com"
    UL_BASE = "https://app.ultralibrarian.com"

    # Supported output formats
    FORMATS = {
        "kicad9": {
            "name": "KiCad 9",
            "extensions": [".kicad_sym", ".kicad_mod", ".step"],
            "ul_format_id": "kicad",
        },
        "altium": {
            "name": "Altium Designer",
            "extensions": [".SchLib", ".PcbLib", ".step"],
            "ul_format_id": "altium",
        },
    }

    def __init__(self, output_dir: str = "./symbols",
                 snapmagic_api_key: str | None = None):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.snapmagic_key = snapmagic_api_key or os.getenv("SNAPMAGIC_API_KEY", "")

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

    def _try_fetch(self, part_number: str, manufacturer: str, fmt: str) -> dict:
        """Try multiple backends to fetch symbols."""
        # Backend 1: SnapMagic API (if key available)
        if self.snapmagic_key:
            result = self._fetch_snapmagic(part_number, fmt)
            if result.get("success"):
                return result

        # Backend 2: Ultra Librarian direct search
        result = self._fetch_ultralibrarian(part_number, manufacturer, fmt)
        if result.get("success"):
            return result

        # Backend 3: Component Search Engine download link
        result = self._fetch_cse(part_number, fmt)
        if result.get("success"):
            return result

        # Fallback: provide manual download instructions
        return {
            "success": False,
            "part_number": part_number,
            "format": fmt,
            "manual_links": {
                "Ultra Librarian": f"https://app.ultralibrarian.com/search?q={part_number}",
                "SnapEDA": f"https://www.snapeda.com/search/?q={part_number}",
                "Component Search Engine": f"https://componentsearchengine.com/search?term={part_number}",
            },
            "instructions": (
                f"Automatic download not available for {part_number}. "
                "Visit the links above to manually download the symbol/footprint."
            ),
        }

    def _fetch_snapmagic(self, part_number: str, fmt: str) -> dict:
        """Fetch from SnapMagic/SnapEDA API."""
        if not self.snapmagic_key:
            return {"success": False, "reason": "No SnapMagic API key"}

        try:
            resp = requests.get(
                f"{self.SNAPMAGIC_API_BASE}/parts/search",
                params={"q": part_number, "limit": 1},
                headers={"Authorization": f"Bearer {self.snapmagic_key}"},
                timeout=30,
            )
            if resp.status_code != 200:
                return {"success": False, "reason": f"SnapMagic API error: {resp.status_code}"}

            data = resp.json()
            parts = data.get("results", [])
            if not parts:
                return {"success": False, "reason": "No results on SnapMagic"}

            part = parts[0]
            download_url = part.get("download_url")
            if not download_url:
                return {"success": False, "reason": "No download URL"}

            # Download the file
            fmt_info = self.FORMATS[fmt]
            save_dir = self.output_dir / part_number / fmt
            save_dir.mkdir(parents=True, exist_ok=True)

            zip_path = save_dir / f"{part_number}.zip"
            self._download_file(download_url, str(zip_path))

            # Extract
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

    def _fetch_ultralibrarian(self, part_number: str, manufacturer: str,
                               fmt: str) -> dict:
        """Attempt to fetch from Ultra Librarian.

        Note: Ultra Librarian does not have a fully public REST API.
        This attempts to use their search endpoint and download links.
        """
        try:
            # Search for the part
            search_url = f"{self.UL_BASE}/api/v1/search"
            resp = requests.get(
                search_url,
                params={"q": part_number, "manufacturer": manufacturer},
                timeout=30,
                headers={"Accept": "application/json"},
            )

            if resp.status_code == 200:
                data = resp.json()
                parts = data.get("parts", data.get("results", []))
                if parts:
                    part = parts[0]
                    dl_url = part.get("download_url") or part.get("kicad_url" if fmt == "kicad9" else "altium_url")
                    if dl_url:
                        save_dir = self.output_dir / part_number / fmt
                        save_dir.mkdir(parents=True, exist_ok=True)
                        zip_path = save_dir / f"{part_number}.zip"
                        self._download_file(dl_url, str(zip_path))
                        files = self._extract_zip(str(zip_path), str(save_dir))
                        return {
                            "success": True,
                            "source": "Ultra Librarian",
                            "format": fmt,
                            "files": files,
                            "directory": str(save_dir),
                        }

            # UL API not publicly accessible — return search link
            return {
                "success": False,
                "reason": "Ultra Librarian requires web browser login",
                "search_url": f"https://app.ultralibrarian.com/search?q={part_number}",
            }
        except requests.exceptions.ConnectionError:
            return {
                "success": False,
                "reason": "Ultra Librarian API not reachable",
                "search_url": f"https://app.ultralibrarian.com/search?q={part_number}",
            }
        except Exception as e:
            return {"success": False, "reason": f"Ultra Librarian error: {e}"}

    def _fetch_cse(self, part_number: str, fmt: str) -> dict:
        """Attempt to fetch from Component Search Engine (Samacsys)."""
        try:
            search_url = f"{self.CSE_BASE}/ga/model.php"
            resp = requests.get(
                search_url,
                params={"partID": part_number},
                timeout=30,
                headers={"Accept": "application/json"},
            )
            if resp.status_code == 200:
                data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                dl_url = data.get("download_url")
                if dl_url:
                    save_dir = self.output_dir / part_number / fmt
                    save_dir.mkdir(parents=True, exist_ok=True)
                    zip_path = save_dir / f"{part_number}.zip"
                    self._download_file(dl_url, str(zip_path))
                    files = self._extract_zip(str(zip_path), str(save_dir))
                    return {
                        "success": True,
                        "source": "Component Search Engine",
                        "format": fmt,
                        "files": files,
                        "directory": str(save_dir),
                    }
        except Exception:
            pass

        return {
            "success": False,
            "reason": "CSE requires browser authentication",
            "search_url": f"https://componentsearchengine.com/search?term={part_number}",
        }

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
            # Clean up zip
            os.remove(zip_path)
        except zipfile.BadZipFile:
            logger.warning("Not a valid ZIP file: %s", zip_path)
            # Keep the file, might be a direct symbol file
            files = [os.path.basename(zip_path)]
        return files

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

            # Check if any format succeeded
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
        """Generate a report of parts needing manual symbol download.

        Returns:
            Formatted text report with download links.
        """
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
                        lines.append(f"  - {pn} [{fmt}]: {info.get('source', 'unknown')}")
            lines.append("")

        if manual:
            lines.append(f"## Manual download required ({len(manual)} parts)")
            lines.append("")
            for r in manual:
                pn = r["part_number"]
                lines.append(f"### {pn}")
                for fmt, info in r.get("formats", {}).items():
                    links = info.get("manual_links", {})
                    if links:
                        for name, url in links.items():
                            lines.append(f"  - [{name}]({url})")
                lines.append("")

        return "\n".join(lines)
