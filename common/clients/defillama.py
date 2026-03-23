"""
DefiLlama client for stablecoin market snapshots.
"""

from dataclasses import dataclass

import aiohttp

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
        self.session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session

    async def close(self) -> None:
        if self.session is not None:
            await self.session.close()
            self.session = None

    async def __aenter__(self) -> "DefiLlamaClient":
        await self._get_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

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

            circulating_value = circulating
            if isinstance(circulating, dict):
                circulating_value = circulating.get("peggedUSD")

            try:
                parsed_price = float(price)
                parsed_circulating = float(circulating_value)
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

    async def fetch_stablecoins(self, top_n: int) -> list[StablecoinSnapshot]:
        session = await self._get_session()
        async with session.get(f"{self.base_url}/stablecoins") as response:
            response.raise_for_status()
            payload = await response.json()
            return self.parse_stablecoins(payload, top_n=top_n)
