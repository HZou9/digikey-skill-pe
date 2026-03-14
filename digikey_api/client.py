"""DigiKey API v4 client with OAuth2 auth and mock mode."""
import logging
import time

import requests

from .cache import Cache
from .config import Config
from .mock_data import (
    mock_keyword_search,
    mock_pricing,
    mock_product_details,
    mock_substitutions,
)

logger = logging.getLogger(__name__)


class DigiKeyClient:
    """DigiKey Product Information API v4 client."""

    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.cache = Cache(self.config.cache_db, self.config.cache_ttl)
        self._access_token: str | None = None
        self._token_expires_at: float = 0
        self._refresh_token: str | None = None

        if self.config.is_mock:
            logger.info("DigiKey client running in MOCK mode (no API credentials)")

    # --- Authentication ---

    def authenticate(self) -> str:
        """Get a valid access token (refresh if needed)."""
        if self._access_token and time.time() < self._token_expires_at - 60:
            return self._access_token

        if self._refresh_token:
            return self._refresh()

        return self._client_credentials()

    def _client_credentials(self) -> str:
        """OAuth2 client credentials flow."""
        resp = requests.post(
            self.config.token_url(),
            data={
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "grant_type": "client_credentials",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + data.get("expires_in", 1799)
        self._refresh_token = data.get("refresh_token")
        logger.info("OAuth2 token acquired (expires in %ds)", data.get("expires_in", 0))
        return self._access_token

    def _refresh(self) -> str:
        """Refresh an expired access token."""
        resp = requests.post(
            self.config.token_url(),
            data={
                "client_id": self.config.client_id,
                "client_secret": self.config.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        self._access_token = data["access_token"]
        self._token_expires_at = time.time() + data.get("expires_in", 1799)
        self._refresh_token = data.get("refresh_token", self._refresh_token)
        return self._access_token

    def _headers(self) -> dict:
        token = self.authenticate()
        return {
            "Authorization": f"Bearer {token}",
            "X-DIGIKEY-Client-Id": self.config.client_id,
            "Content-Type": "application/json",
        }

    def _api_request(self, method: str, url: str, json_body: dict | None = None,
                     cache_key: str | None = None, retries: int = 2) -> dict:
        """Make an API request with caching and retry on 429."""
        if cache_key:
            cached = self.cache.get(cache_key)
            if cached:
                logger.debug("Cache hit for %s", cache_key[:16])
                return cached

        for attempt in range(retries + 1):
            resp = requests.request(
                method, url, headers=self._headers(), json=json_body, timeout=30
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 5))
                logger.warning("Rate limited, retrying in %ds", retry_after)
                time.sleep(retry_after)
                continue
            resp.raise_for_status()
            data = resp.json()
            if cache_key:
                self.cache.set(cache_key, data)
            return data

        raise RuntimeError(f"API request failed after {retries + 1} attempts")

    # --- Public API ---

    def keyword_search(
        self,
        keywords: str,
        limit: int = 10,
        offset: int = 0,
        filters: dict | None = None,
        sort: str | None = None,
        in_stock: bool = True,
    ) -> dict:
        """Search DigiKey product catalog by keyword.

        Args:
            keywords: Search query string
            limit: Max results (1-50)
            offset: Pagination offset
            filters: Additional parametric filters
            sort: Sort field (e.g., "UnitPrice", "QuantityAvailable")
            in_stock: Only return in-stock items

        Returns:
            Dict with Products list, ProductsCount, etc.
        """
        if self.config.is_mock:
            return mock_keyword_search(keywords, limit)

        body = {
            "Keywords": keywords,
            "Limit": min(limit, 50),
            "Offset": offset,
        }
        if filters:
            body["Filters"] = filters
        if sort:
            body["Sort"] = {"SortOption": sort, "Direction": "Ascending"}
        if in_stock:
            body["FilterOptionsRequest"] = {"InStock": True}

        cache_key = Cache.make_key("keyword_search", body)
        data = self._api_request("POST", self.config.search_url(), body, cache_key)
        data["Products"] = [self._normalize_product(p) for p in data.get("Products", [])]
        return data

    def product_details(self, digikey_part_number: str) -> dict:
        """Get detailed product information.

        Args:
            digikey_part_number: DigiKey part number (e.g., "C3M0025065K-ND")

        Returns:
            Dict with full product details including parameters.
        """
        if self.config.is_mock:
            return mock_product_details(digikey_part_number)

        url = self.config.details_url(digikey_part_number)
        cache_key = Cache.make_key("details", {"pn": digikey_part_number})
        data = self._api_request("GET", url, cache_key=cache_key)
        if "Product" in data:
            data["Product"] = self._normalize_product(data["Product"])
        return data

    def get_pricing(self, digikey_part_number: str) -> dict:
        """Get pricing tiers for a product.

        Args:
            digikey_part_number: DigiKey part number

        Returns:
            Dict with PricingTiers list.
        """
        if self.config.is_mock:
            return mock_pricing(digikey_part_number)

        url = self.config.pricing_url(digikey_part_number)
        cache_key = Cache.make_key("pricing", {"pn": digikey_part_number})
        return self._api_request("GET", url, cache_key=cache_key)

    def search_substitutions(self, digikey_part_number: str) -> dict:
        """Find substitute products.

        Args:
            digikey_part_number: DigiKey part number

        Returns:
            Dict with Substitutions list.
        """
        if self.config.is_mock:
            return mock_substitutions(digikey_part_number)

        url = self.config.substitutions_url(digikey_part_number)
        cache_key = Cache.make_key("subs", {"pn": digikey_part_number})
        return self._api_request("GET", url, cache_key=cache_key)

    # --- Response normalization ---

    @staticmethod
    def _normalize_product(p: dict) -> dict:
        """Normalize real API response fields to match mock data format.

        Real API uses ManufacturerProductNumber, ParameterText/ValueText, etc.
        Mock data uses ManufacturerPartNumber, Name/Value, etc.
        This ensures downstream code works with both.
        """
        # Already normalized (mock data) — skip
        if "ManufacturerPartNumber" in p and isinstance(p.get("Description", ""), str):
            return p

        # ManufacturerProductNumber → ManufacturerPartNumber
        if "ManufacturerProductNumber" in p and "ManufacturerPartNumber" not in p:
            p["ManufacturerPartNumber"] = p["ManufacturerProductNumber"]

        # Manufacturer dict → string
        mfr = p.get("Manufacturer")
        if isinstance(mfr, dict):
            p["Manufacturer"] = mfr.get("Name", str(mfr))

        # Description dict → string
        desc = p.get("Description")
        if isinstance(desc, dict):
            p["Description"] = desc.get("DetailedDescription",
                                        desc.get("ProductDescription", ""))

        # DigiKeyPartNumber from ProductVariations
        if "DigiKeyPartNumber" not in p:
            variations = p.get("ProductVariations", [])
            if variations:
                # Prefer Cut Tape or first available
                for v in variations:
                    pkg = v.get("PackageType", {}).get("Name", "")
                    if "Cut Tape" in pkg or "Through Hole" in pkg:
                        p["DigiKeyPartNumber"] = v["DigiKeyProductNumber"]
                        break
                if "DigiKeyPartNumber" not in p:
                    p["DigiKeyPartNumber"] = variations[0].get("DigiKeyProductNumber", "")

        # Parameters: ParameterText/ValueText → Name/Value
        params = p.get("Parameters", [])
        normalized_params = []
        for param in params:
            if "ParameterText" in param:
                normalized_params.append({
                    "Name": param["ParameterText"],
                    "Value": param["ValueText"],
                })
            else:
                normalized_params.append(param)
        p["Parameters"] = normalized_params

        return p

    def download_datasheet(self, url: str, save_path: str) -> str:
        """Download a datasheet PDF.

        Args:
            url: Datasheet URL from product details
            save_path: Local path to save the PDF

        Returns:
            Path to saved file.
        """
        resp = requests.get(url, timeout=60, stream=True)
        resp.raise_for_status()
        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        logger.info("Downloaded datasheet to %s", save_path)
        return save_path
