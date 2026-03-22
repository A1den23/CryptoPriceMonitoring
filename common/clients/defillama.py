"""
DefiLlama client for stablecoin market snapshots.
"""

from dataclasses import dataclass

import requests

from ..logging import logger


EXCLUDED_STABLECOIN_SYMBOLS = {"USYC", "USDY"}


@dataclass(frozen=True, slots=True)
class StablecoinSnapshot:
    name: str
    symbol: str
    price: float
    circulating: float
    rank: int


class DefiLlamaClient:
    """Read-only client for DefiLlama stablecoin data."""

    def __init__(self, base_url: str = "https://stablecoins.llama.fi", timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> "DefiLlamaClient":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def parse_stablecoins(self, payload: dict, top_n: int) -> list[StablecoinSnapshot]:
        pegged_assets = payload.get("peggedAssets")
        if not isinstance(pegged_assets, list):
            logger.error("Invalid DefiLlama payload: missing peggedAssets list")
            raise ValueError("Invalid DefiLlama payload")

        snapshots: list[StablecoinSnapshot] = []
        for asset in pegged_assets:
            if not isinstance(asset, dict):
                continue

            name = asset.get("name")
            symbol = asset.get("symbol")
            price = asset.get("price")
            circulating = asset.get("circulating")

            if not name or not symbol:
                continue

            try:
                parsed_price = float(price)
                parsed_circulating = float(circulating)
            except (TypeError, ValueError):
                continue

            parsed_symbol = str(symbol)
            if parsed_symbol.upper() in EXCLUDED_STABLECOIN_SYMBOLS:
                continue

            snapshots.append(
                StablecoinSnapshot(
                    name=str(name),
                    symbol=parsed_symbol,
                    price=parsed_price,
                    circulating=parsed_circulating,
                    rank=0,
                )
            )

        snapshots.sort(key=lambda item: item.circulating, reverse=True)
        ranked = [
            StablecoinSnapshot(
                name=item.name,
                symbol=item.symbol,
                price=item.price,
                circulating=item.circulating,
                rank=index,
            )
            for index, item in enumerate(snapshots[:top_n], start=1)
        ]
        return ranked

    def fetch_stablecoins(self, top_n: int) -> list[StablecoinSnapshot]:
        response = self.session.get(f"{self.base_url}/stablecoins", timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        return self.parse_stablecoins(payload, top_n=top_n)
