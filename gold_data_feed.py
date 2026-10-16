import time
import numpy as np
import requests
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable, Tuple

from ict_market_structure import ICTMarketStructureEngine


class GoldDataFeed:
    BASE_URL = "https://biquote.io"
    SYMBOL = "XAUUSD"

    def __init__(
        self,
        max_bars: int = 2000,
        interval: str = "1h",
        engine: Optional[ICTMarketStructureEngine] = None,
        engine_kwargs: Optional[Dict[str, Any]] = None,
        request_timeout: float = 10.0,
    ):
        self.max_bars = int(max_bars)
        self.interval = interval
        self.request_timeout = float(request_timeout)
        self.engine = engine if engine is not None else ICTMarketStructureEngine(**(engine_kwargs or {}))

        self._buffer = np.empty((self.max_bars, 4), dtype=np.float64)
        self._write_idx = 0
        self._count = 0
        self._last_open_time: Optional[str] = None
        self._last_tick: Optional[Dict[str, Any]] = None
        self._last_state: Dict[str, Any] = {}
        self._running = False

    def _push(self, o: float, h: float, l: float, c: float) -> None:
        self._buffer[self._write_idx, 0] = o
        self._buffer[self._write_idx, 1] = h
        self._buffer[self._write_idx, 2] = l
        self._buffer[self._write_idx, 3] = c
        self._write_idx = (self._write_idx + 1) % self.max_bars
        if self._count < self.max_bars:
            self._count += 1

    def _snapshot_arrays(self) -> Optional[np.ndarray]:
        n = self._count
        if n == 0:
            return None
        start = (self._write_idx - n) % self.max_bars
        if start + n <= self.max_bars:
            return self._buffer[start:start + n]
        tail = self._buffer[start:]
        head = self._buffer[:(start + n) % self.max_bars]
        return np.concatenate([tail, head])

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        r = requests.get(f"{self.BASE_URL}{path}", params=params, timeout=self.request_timeout)
        r.raise_for_status()
        return r.json()

    def fetch_ohlc(self, limit: int = 1000, to_time: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"interval": self.interval, "limit": int(limit)}
        if to_time is not None:
            params["to"] = to_time
        data = self._get(f"/api/{self.SYMBOL}/ohlc", params)
        bars = data.get("bars", [])
        return [b for b in bars if not b.get("isOpen", False)]

    def fetch_tick(self) -> Dict[str, Any]:
        return self._get(f"/api/{self.SYMBOL}")

    def bootstrap(self) -> int:
        collected: List[Dict[str, Any]] = []
        to_time: Optional[str] = None
        remaining = self.max_bars
        seen = set()
        while remaining > 0:
            chunk = min(remaining, 1000)
            bars = self.fetch_ohlc(limit=chunk, to_time=to_time)
            if not bars:
                break
            fresh = [b for b in bars if b["openTime"] not in seen]
            if not fresh:
                break
            for b in fresh:
                seen.add(b["openTime"])
            collected = fresh + collected
            to_time = fresh[0]["openTime"]
            remaining -= len(fresh)
            if len(fresh) < chunk:
                break
        for b in collected[-self.max_bars:]:
            self._push(float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]))
        if collected:
            self._last_open_time = collected[-1]["openTime"]
        return self._count

    def update(self, lookback_bars: int = 10) -> int:
        bars = self.fetch_ohlc(limit=max(lookback_bars, 2))
        new_bars = [
            b for b in bars
            if self._last_open_time is None or b["openTime"] > self._last_open_time
        ]
        for b in new_bars:
            self._push(float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"]))
            self._last_open_time = b["openTime"]
        return len(new_bars)

    def update_tick(self) -> Dict[str, Any]:
        self._last_tick = self.fetch_tick()
        return self._last_tick

    def analyze(self) -> Dict[str, Any]:
        arrs = self._snapshot_arrays()
        if arrs is None or len(arrs) < 2:
            self._last_state = self.engine.analyze(np.array([], dtype=np.float64))
            return self._last_state
        o = arrs[:, 0]
        h = arrs[:, 1]
        l = arrs[:, 2]
        c = arrs[:, 3]
        self._last_state = self.engine.analyze((o, h, l, c))
        return self._last_state

    def step(self, lookback_bars: int = 10) -> Tuple[int, Dict[str, Any], Dict[str, Any]]:
        new_bars = self.update(lookback_bars)
        tick = self.update_tick()
        state = self.analyze()
        return new_bars, tick, state

    def run_forever(
        self,
        poll_seconds: float = 1.0,
        lookback_bars: int = 10,
        on_update: Optional[Callable[[int, Dict[str, Any], Dict[str, Any]], None]] = None,
    ) -> None:
        self.bootstrap()
        self._running = True
        while self._running:
            try:
                new_bars, tick, state = self.step(lookback_bars)
                if on_update is not None:
                    on_update(new_bars, tick, state)
            except requests.RequestException:
                pass
            time.sleep(poll_seconds)

    def stop(self) -> None:
        self._running = False

    def snapshot_arrays(self) -> Optional[np.ndarray]:
        return self._snapshot_arrays()

    def state(self) -> Dict[str, Any]:
        return dict(self._last_state)

    def current_price(self) -> Optional[float]:
        if self._last_tick is None:
            return None
        return float(self._last_tick.get("mid") or self._last_tick.get("bid") or 0.0)

    @property
    def bar_count(self) -> int:
        return self._count
