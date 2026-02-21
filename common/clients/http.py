"""
HTTP clients for Binance API
"""

import asyncio

import aiohttp
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from ..logging import logger


class BinanceAPIError(Exception):
    """Custom exception for Binance API errors"""
    pass


class BinancePriceFetcher:
    """Fetch prices from Binance API with retry mechanism"""

    def __init__(self, base_url: str = "https://api.binance.com/api/v3"):
        self.base_url = base_url
        self.session = requests.Session()
        # Set connection pool and timeouts
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=0  # We handle retries with tenacity
        )
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)

    def _handle_request_error(self, symbol: str, error: Exception) -> None:
        """Log and convert request errors to BinanceAPIError."""
        logger.error(f"Error fetching price for {symbol}: {error}")
        raise BinanceAPIError(f"Failed to fetch price for {symbol}") from error

    def _handle_response_error(self, symbol: str, error: Exception) -> None:
        """Log and convert response parsing errors to BinanceAPIError."""
        logger.error(f"Invalid response for {symbol}: {error}")
        raise BinanceAPIError(f"Invalid response format for {symbol}") from error

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((requests.exceptions.RequestException, BinanceAPIError)),
        reraise=True,
    )
    def get_current_price(self, symbol: str) -> float | None:
        """Get current price from Binance with retry mechanism."""
        try:
            url = f"{self.base_url}/ticker/price"
            params = {"symbol": symbol}
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            return float(data["price"])
        except requests.exceptions.RequestException as e:
            self._handle_request_error(symbol, e)
        except (KeyError, ValueError) as e:
            self._handle_response_error(symbol, e)

    def close(self) -> None:
        """Close the session."""
        self.session.close()

    def __enter__(self) -> 'BinancePriceFetcher':
        """Support for 'with' statement."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Close session when exiting 'with' block."""
        self.close()


class AsyncBinancePriceFetcher:
    """Async fetcher for concurrent price requests"""

    def __init__(self, base_url: str = "https://api.binance.com/api/v3"):
        self.base_url = base_url
        self.session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> 'AsyncBinancePriceFetcher':
        """Create async session when entering context."""
        timeout = aiohttp.ClientTimeout(total=10)
        self.session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Close async session when exiting context."""
        if self.session:
            await self.session.close()

    async def _handle_request_error(self, symbol: str, error: Exception) -> None:
        """Log and convert request errors to BinanceAPIError."""
        logger.error(f"Error fetching price for {symbol}: {error}")
        raise BinanceAPIError(f"Failed to fetch price for {symbol}") from error

    async def _handle_response_error(self, symbol: str, error: Exception) -> None:
        """Log and convert response parsing errors to BinanceAPIError."""
        logger.error(f"Invalid response for {symbol}: {error}")
        raise BinanceAPIError(f"Invalid response format for {symbol}") from error

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, BinanceAPIError)),
        reraise=True,
    )
    async def get_current_price(self, symbol: str) -> float | None:
        """Get current price asynchronously with retry mechanism."""
        if not self.session:
            raise RuntimeError("Session not initialized. Use async context manager.")

        try:
            url = f"{self.base_url}/ticker/price"
            params = {"symbol": symbol}
            async with self.session.get(url, params=params) as response:
                response.raise_for_status()
                data = await response.json()
                return float(data["price"])
        except aiohttp.ClientError as e:
            await self._handle_request_error(symbol, e)
        except (KeyError, ValueError) as e:
            await self._handle_response_error(symbol, e)
        return None  # Unreachable due to raise, but satisfies type checker

    async def get_multiple_prices(self, symbols: list[str]) -> dict[str, float | None]:
        """Fetch multiple prices concurrently."""
        tasks = {
            symbol: asyncio.create_task(self.get_current_price(symbol))
            for symbol in symbols
        }
        results: dict[str, float | None] = {}

        completed = await asyncio.gather(*tasks.values(), return_exceptions=True)
        for symbol, result in zip(tasks.keys(), completed, strict=False):
            if isinstance(result, Exception):
                logger.error(f"Failed to fetch {symbol} after retries: {result}")
                results[symbol] = None
            else:
                results[symbol] = result

        return results
