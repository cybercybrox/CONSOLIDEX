import numpy as np
from numba import njit
from typing import Dict, Any, Tuple, Union

@njit(cache=True)
def _swing_points(high: np.ndarray, low: np.ndarray, lookback: int) -> Tuple[np.ndarray, np.ndarray]:
    n = len(high)
    swing_high = np.zeros(n, dtype=np.bool_)
    swing_low = np.zeros(n, dtype=np.bool_)
    for i in range(lookback, n - lookback):
        h = high[i]
        l = low[i]
        is_high = True
        is_low = True
        for j in range(i - lookback, i + lookback + 1):
            if high[j] > h:
                is_high = False
            if low[j] < l:
                is_low = False
        swing_high[i] = is_high
        swing_low[i] = is_low
    return swing_high, swing_low

@njit(cache=True)
def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> np.ndarray:
    n = len(high)
    tr = np.empty(n, dtype=np.float64)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i - 1])
        lc = abs(low[i] - close[i - 1])
        tr[i] = max(hl, hc, lc)
    atr = np.full(n, np.nan, dtype=np.float64)
    if n >= period:
        atr[period - 1] = np.mean(tr[:period])
        alpha = 1.0 / period
        for i in range(period, n):
            atr[i] = atr[i - 1] + alpha * (tr[i] - atr[i - 1])
    return atr

@njit(cache=True)
def _consolidation_score(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    atr: np.ndarray,
    box_lookback: int,
    atr_mult: float,
) -> np.ndarray:
    n = len(high)
    score = np.zeros(n, dtype=np.float64)
    for i in range(box_lookback, n):
        window_high = high[i - box_lookback]
        window_low = low[i - box_lookback]
        for k in range(i - box_lookback + 1, i + 1):
            if high[k] > window_high:
                window_high = high[k]
            if low[k] < window_low:
                window_low = low[k]
        span = window_high - window_low
        atr_val = atr[i]
        if np.isnan(atr_val) or atr_val == 0.0:
            continue
        range_score = 1.0 - min(span / (atr_val * atr_mult), 1.0)
        inside = 0
        for j in range(i - box_lookback + 1, i + 1):
            if high[j] <= high[j - 1] and low[j] >= low[j - 1]:
                inside += 1
        inside_score = inside / box_lookback
        total_move = 0.0
        for k in range(i - box_lookback + 1, i + 1):
            total_move += abs(close[k] - close[k - 1])
        avg_move = total_move / box_lookback
        move_score = 1.0 - min(avg_move / atr_val, 1.0)
        score[i] = (range_score + inside_score + move_score) / 3.0
    return score

@njit(cache=True)
def _synthesize_ohlc(close: np.ndarray, win: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = len(close)
    out_len = n - win + 1
    open_ = np.empty(out_len, dtype=np.float64)
    high = np.empty(out_len, dtype=np.float64)
    low = np.empty(out_len, dtype=np.float64)
    for i in range(out_len):
        window = close[i:i + win]
        open_[i] = window[0]
        high[i] = window[0]
        low[i] = window[0]
        for k in range(1, win):
            v = window[k]
            if v > high[i]:
                high[i] = v
            if v < low[i]:
                low[i] = v
    return open_, high, low

class ICTMarketStructureEngine:

    def __init__(
        self,
        swing_lookback: int = 5,
        atr_period: int = 14,
        box_lookback: int = 20,
        atr_mult: float = 1.5,
        min_consolidation_score: float = 0.65,
    ):
        self.swing_lookback = int(swing_lookback)
        self.atr_period = int(atr_period)
        self.box_lookback = int(box_lookback)
        self.atr_mult = float(atr_mult)
        self.min_consolidation_score = float(min_consolidation_score)
        self._state: Dict[str, Any] = self._blank_state()

    @staticmethod
    def _blank_state() -> Dict[str, Any]:
        return {
            "trend": "UNDEFINED",
            "last_swing_high": None,
            "last_swing_low": None,
            "bos_direction": None,
            "choch_direction": None,
            "liquidity_sweep": None,
            "displacement": None,
            "consolidation_score": 0.0,
            "consolidation_active": False,
            "consolidation_start_index": None,
            "consolidation_resistance": None,
            "consolidation_support": None,
            "premium_discount": None,
            "last_atr": None,
        }

    def analyze(
        self,
        prices: Union[np.ndarray, Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]],
    ) -> Dict[str, Any]:
        if isinstance(prices, tuple):
            if len(prices) != 4:
                raise ValueError("OHLC tuple must contain (open, high, low, close)")
            open_, high, low, close = prices
            open_ = np.ascontiguousarray(open_, dtype=np.float64)
            high = np.ascontiguousarray(high, dtype=np.float64)
            low = np.ascontiguousarray(low, dtype=np.float64)
            close = np.ascontiguousarray(close, dtype=np.float64)
            if not (len(open_) == len(high) == len(low) == len(close)):
                raise ValueError("OHLC arrays must all be the same length")
        else:
            close = np.ascontiguousarray(prices, dtype=np.float64)
            win = max(2, self.swing_lookback)
            if close.ndim != 1 or len(close) < win + 1:
                return self._snapshot()
            open_, high, low = _synthesize_ohlc(close, win)

        n = len(close)
        min_required = max(self.swing_lookback * 2 + 1, self.atr_period + 1, self.box_lookback + 1)
        if n < min_required:
            return self._snapshot()

        atr = _atr(high, low, close, self.atr_period)
        swing_high_mask, swing_low_mask = _swing_points(high, low, self.swing_lookback)

        sh_indices = np.where(swing_high_mask)[0]
        sl_indices = np.where(swing_low_mask)[0]

        if len(sh_indices) >= 2 and len(sl_indices) >= 2:
            last_sh = sh_indices[-1]
            prev_sh = sh_indices[-2]
            last_sl = sl_indices[-1]
            prev_sl = sl_indices[-2]

            if high[last_sh] > high[prev_sh] and low[last_sl] > low[prev_sl]:
                trend = "UPTREND"
            elif high[last_sh] < high[prev_sh] and low[last_sl] < low[prev_sl]:
                trend = "DOWNTREND"
            else:
                trend = "RANGING"

            self._state["trend"] = trend
            self._state["last_swing_high"] = float(high[last_sh])
            self._state["last_swing_low"] = float(low[last_sl])

            current_close = close[-1]
            bos_dir = None
            choch_dir = None

            if trend == "UPTREND":
                if current_close > high[last_sh]:
                    bos_dir = "BULLISH"
                elif current_close < low[last_sl]:
                    choch_dir = "BEARISH"
            elif trend == "DOWNTREND":
                if current_close < low[last_sl]:
                    bos_dir = "BEARISH"
                elif current_close > high[last_sh]:
                    choch_dir = "BULLISH"

            self._state["bos_direction"] = bos_dir
            self._state["choch_direction"] = choch_dir

            last_sh_price = high[sh_indices[-1]]
            last_sl_price = low[sl_indices[-1]]
            sweep = None
            if high[-1] > last_sh_price and close[-1] < last_sh_price:
                sweep = "BEARISH"
            elif low[-1] < last_sl_price and close[-1] > last_sl_price:
                sweep = "BULLISH"
            self._state["liquidity_sweep"] = sweep

            disp = None
            if not np.isnan(atr[-1]) and atr[-1] > 0:
                body = abs(close[-1] - open_[-1])
                if body > atr[-1] * 1.5:
                    disp = "BULLISH" if close[-1] > open_[-1] else "BEARISH"
            self._state["displacement"] = disp

            if last_sh > last_sl:
                range_high = high[last_sh]
                range_low = low[last_sl]
                if range_high > range_low:
                    eq = (range_high + range_low) / 2.0
                    if close[-1] > eq:
                        pd = "PREMIUM"
                    elif close[-1] < eq:
                        pd = "DISCOUNT"
                    else:
                        pd = "EQUILIBRIUM"
                    self._state["premium_discount"] = pd

        cons_score = _consolidation_score(high, low, close, atr, self.box_lookback, self.atr_mult)
        current_score = float(cons_score[-1]) if not np.isnan(cons_score[-1]) else 0.0
        self._state["consolidation_score"] = current_score

        if current_score >= self.min_consolidation_score:
            if not self._state["consolidation_active"]:
                self._state["consolidation_active"] = True
                self._state["consolidation_start_index"] = n - self.box_lookback
                window_high = high[n - self.box_lookback]
                window_low = low[n - self.box_lookback]
                for k in range(n - self.box_lookback + 1, n):
                    if high[k] > window_high:
                        window_high = high[k]
                    if low[k] < window_low:
                        window_low = low[k]
                self._state["consolidation_resistance"] = float(window_high)
                self._state["consolidation_support"] = float(window_low)
        else:
            if self._state["consolidation_active"]:
                res = self._state["consolidation_resistance"]
                sup = self._state["consolidation_support"]
                if res is not None and sup is not None:
                    if close[-1] > res or close[-1] < sup:
                        self._state["consolidation_active"] = False
                        self._state["consolidation_start_index"] = None
                        self._state["consolidation_resistance"] = None
                        self._state["consolidation_support"] = None

        self._state["last_atr"] = float(atr[-1]) if not np.isnan(atr[-1]) else None
        return self._snapshot()

    def _snapshot(self) -> Dict[str, Any]:
        return dict(self._state)

    def reset(self) -> None:
        self._state = self._blank_state()
