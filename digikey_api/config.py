"""DigiKey API configuration management."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass


class Config:
    """DigiKey API configuration."""

    SANDBOX_BASE_URL = "https://sandbox-api.digikey.com"
    PRODUCTION_BASE_URL = "https://api.digikey.com"
    TOKEN_ENDPOINT = "/v1/oauth2/token"
    SEARCH_ENDPOINT = "/products/v4/search/keyword"
    DETAILS_ENDPOINT = "/products/v4/search/{part_number}/productdetails"
    PRICING_ENDPOINT = "/products/v4/search/{part_number}/pricing"
    SUBSTITUTIONS_ENDPOINT = "/products/v4/search/{part_number}/substitutions"
    MANUFACTURERS_ENDPOINT = "/products/v4/search/manufacturers"
    CATEGORIES_ENDPOINT = "/products/v4/search/categories"
    MEDIA_ENDPOINT = "/products/v4/search/{part_number}/media"

    def __init__(self):
        self.client_id = os.getenv("DIGIKEY_CLIENT_ID", "")
        self.client_secret = os.getenv("DIGIKEY_CLIENT_SECRET", "")
        self.use_sandbox = os.getenv("DIGIKEY_USE_SANDBOX", "true").lower() == "true"
        self.mock_mode = os.getenv("DIGIKEY_MOCK_MODE", "auto")  # auto, true, false
        self.cache_ttl = int(os.getenv("DIGIKEY_CACHE_TTL", "86400"))
        self.cache_db = os.getenv(
            "DIGIKEY_CACHE_DB",
            str(Path(__file__).parent.parent / "cache.db"),
        )

    @property
    def base_url(self) -> str:
        return self.SANDBOX_BASE_URL if self.use_sandbox else self.PRODUCTION_BASE_URL

    @property
    def is_mock(self) -> bool:
        if self.mock_mode == "true":
            return True
        if self.mock_mode == "false":
            return False
        # auto: mock if no credentials
        return not (self.client_id and self.client_secret)

    def token_url(self) -> str:
        return self.base_url + self.TOKEN_ENDPOINT

    def search_url(self) -> str:
        return self.base_url + self.SEARCH_ENDPOINT

    def details_url(self, part_number: str) -> str:
        return self.base_url + self.DETAILS_ENDPOINT.format(part_number=part_number)

    def pricing_url(self, part_number: str) -> str:
        return self.base_url + self.PRICING_ENDPOINT.format(part_number=part_number)

    def substitutions_url(self, part_number: str) -> str:
        return self.base_url + self.SUBSTITUTIONS_ENDPOINT.format(part_number=part_number)
