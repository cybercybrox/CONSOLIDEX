import httpx
import orjson
from typing import List, Dict, Optional, Any
from datetime import datetime, timezone
from functools import lru_cache
import asyncio

BASE_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

class USDNewsFetcher:
    def __init__(self, timeout: float = 10.0, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = httpx.Client(
            headers=HEADERS,
            timeout=httpx.Timeout(timeout),
            http2=True,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
        )
        self._async_client: Optional[httpx.AsyncClient] = None

    def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None or self._async_client.is_closed:
            self._async_client = httpx.AsyncClient(
                headers=HEADERS,
                timeout=httpx.Timeout(self.timeout),
                http2=True,
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
            )
        return self._async_client

    def _parse_event(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        dt_str = raw.get("date", "")
        try:
            dt = datetime.fromisoformat(dt_str)
        except:
            dt = None

        return {
            "title": raw.get("title", "").strip(),
            "country": raw.get("country", ""),
            "impact": raw.get("impact", ""),
            "date": dt_str,
            "datetime": dt,
            "forecast": raw.get("forecast", ""),
            "previous": raw.get("previous", ""),
            "actual": raw.get("actual", "") if "actual" in raw else None,
            "is_high_impact": raw.get("impact") == "High",
            "is_usd": raw.get("country") == "USD",
            "timestamp": dt.timestamp() if dt else 0
        }

    def fetch_raw(self) -> List[Dict[str, Any]]:
        for _ in range(self.max_retries):
            try:
                resp = self._client.get(BASE_URL)
                resp.raise_for_status()
                return orjson.loads(resp.content)
            except Exception:
                continue
        return []

    def fetch_usd_this_week(self, high_impact_only: bool = False) -> Dict[str, Any]:
        raw_data = self.fetch_raw()
        usd_events = [self._parse_event(e) for e in raw_data if e.get("country") == "USD"]

        if high_impact_only:
            usd_events = [e for e in usd_events if e["is_high_impact"]]

        usd_events.sort(key=lambda x: x["timestamp"])

        now = datetime.now(timezone.utc)
        past = [e for e in usd_events if e["datetime"] and e["datetime"] < now]
        upcoming = [e for e in usd_events if e["datetime"] and e["datetime"] >= now]
        without_date = [e for e in usd_events if not e["datetime"]]

        return {
            "meta": {
                "total_usd": len(usd_events),
                "past_count": len(past),
                "upcoming_count": len(upcoming),
                "fetched_at": now.isoformat(),
                "source": BASE_URL
            },
            "past_this_week": past,
            "upcoming_this_week": upcoming,
            "without_date": without_date,
            "all_usd_this_week": usd_events
        }

    async def fetch_raw_async(self) -> List[Dict[str, Any]]:
        client = self._get_async_client()
        for _ in range(self.max_retries):
            try:
                resp = await client.get(BASE_URL)
                resp.raise_for_status()
                return orjson.loads(resp.content)
            except Exception:
                continue
        return []

    async def fetch_usd_this_week_async(self, high_impact_only: bool = False) -> Dict[str, Any]:
        raw_data = await self.fetch_raw_async()
        usd_events = [self._parse_event(e) for e in raw_data if e.get("country") == "USD"]

        if high_impact_only:
            usd_events = [e for e in usd_events if e["is_high_impact"]]

        usd_events.sort(key=lambda x: x["timestamp"])

        now = datetime.now(timezone.utc)
        past = [e for e in usd_events if e["datetime"] and e["datetime"] < now]
        upcoming = [e for e in usd_events if e["datetime"] and e["datetime"] >= now]
        without_date = [e for e in usd_events if not e["datetime"]]

        return {
            "meta": {
                "total_usd": len(usd_events),
                "past_count": len(past),
                "upcoming_count": len(upcoming),
                "fetched_at": now.isoformat(),
                "source": BASE_URL
            },
            "past_this_week": past,
            "upcoming_this_week": upcoming,
            "without_date": without_date,
            "all_usd_this_week": usd_events
        }

    def close(self):
        if hasattr(self, '_client'):
            self._client.close()

    async def aclose(self):
        if self._async_client and not self._async_client.is_closed:
            await self._async_client.aclose()

_fetcher_instance: Optional[USDNewsFetcher] = None

def get_fetcher() -> USDNewsFetcher:
    global _fetcher_instance
    if _fetcher_instance is None:
        _fetcher_instance = USDNewsFetcher()
    return _fetcher_instance

@lru_cache(maxsize=1)
def get_usd_news_cached(high_impact_only: bool = False) -> Dict[str, Any]:
    return get_fetcher().fetch_usd_this_week(high_impact_only=high_impact_only)

def get_usd_news(high_impact_only: bool = False) -> Dict[str, Any]:
    return get_fetcher().fetch_usd_this_week(high_impact_only=high_impact_only)

def get_usd_high_impact() -> Dict[str, Any]:
    return get_fetcher().fetch_usd_this_week(high_impact_only=True)

async def get_usd_news_async(high_impact_only: bool = False) -> Dict[str, Any]:
    return await get_fetcher().fetch_usd_this_week_async(high_impact_only=high_impact_only)

def clear_cache():
    get_usd_news_cached.cache_clear()

if __name__ == "__main__":
    result = get_usd_news()
    print(orjson.dumps(result, option=orjson.OPT_INDENT_2).decode())