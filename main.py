import os
import asyncio
import threading
import logging
from queue import Queue, Empty
from typing import Optional, Dict, Any, List, Set

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

from gold_data_feed import GoldDataFeed


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("gold_bot")


BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN",
    "8987983309:AAFVpY47PLTQyqlDh7NpJeCAUMubApQ0EXw",
)
ALLOWED_CHAT_IDS: Set[int] = {
    int(x) for x in os.environ.get("ALLOWED_CHAT_IDS", "").split(",")
    if x.strip().lstrip("-").isdigit()
}
DEFAULT_INTERVAL = os.environ.get("DEFAULT_INTERVAL", "1h")
MAX_BARS = int(os.environ.get("MAX_BARS", "2000"))
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "1.0"))
CONS_THRESHOLD = float(os.environ.get("CONS_THRESHOLD", "0.65"))

INTERVALS_ORDER = ["1m", "5m", "15m", "30m", "1h", "4h", "1d"]


def _price(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.2f}"
    except Exception:
        return "—"


def _score(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.3f}"
    except Exception:
        return "—"


def _authorized(update: Update) -> bool:
    if not ALLOWED_CHAT_IDS:
        return True
    chat = update.effective_chat
    return chat is not None and chat.id in ALLOWED_CHAT_IDS


class FeedController:

    def __init__(self, max_bars: int = MAX_BARS, interval: str = DEFAULT_INTERVAL):
        self.max_bars = int(max_bars)
        self.interval = interval
        self.feed: Optional[GoldDataFeed] = None
        self.thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.events: Queue = Queue()
        self.subscribers: Set[int] = set()
        self._prev: Dict[str, Any] = {}
        self.last_state: Dict[str, Any] = {}

    def start(self) -> None:
        with self._lock:
            if self.thread and self.thread.is_alive():
                return
            self._stop.clear()
            self._build_feed()
            self.thread = threading.Thread(target=self._loop, name="gold-feed", daemon=True)
            self.thread.start()

    def _build_feed(self) -> None:
        self.feed = GoldDataFeed(max_bars=self.max_bars, interval=self.interval)
        try:
            self.feed.bootstrap()
        except Exception as e:
            logger.exception("bootstrap failed")
            try:
                self.feed._record_error("bootstrap", str(e))
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()
        t = self.thread
        if t and t.is_alive():
            t.join(timeout=5)
        self.thread = None

    def switch(self, interval: str) -> None:
        if interval not in GoldDataFeed.VALID_INTERVALS:
            raise ValueError(f"invalid interval: {interval}")
        self.stop()
        with self._lock:
            self.interval = interval
            self._prev = {}
            self.last_state = {}
        self.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                feed = self.feed
                if feed is None:
                    self._stop.wait(0.5)
                    continue
                feed.update(lookback_bars=5)
                feed.update_tick()
                state = feed.analyze()
                self.last_state = state
                self._diff_and_queue(state)
            except Exception as e:
                logger.exception("feed loop error")
                try:
                    if self.feed is not None:
                        self.feed._record_error("loop", str(e))
                except Exception:
                    pass
                self._stop.wait(1.0)
                continue
            self._stop.wait(POLL_SECONDS)

    def _diff_and_queue(self, state: Dict[str, Any]) -> None:
        prev = self._prev
        if prev:
            if prev.get("trend") != state.get("trend"):
                self.events.put(("TREND", state.get("trend"), state))
            if state.get("bos_direction") and prev.get("bos_direction") != state.get("bos_direction"):
                self.events.put(("BOS", state.get("bos_direction"), state))
            if state.get("choch_direction") and prev.get("choch_direction") != state.get("choch_direction"):
                self.events.put(("CHOCH", state.get("choch_direction"), state))
            if state.get("liquidity_sweep") and prev.get("liquidity_sweep") != state.get("liquidity_sweep"):
                self.events.put(("SWEEP", state.get("liquidity_sweep"), state))
            if prev.get("consolidation_active") != state.get("consolidation_active"):
                self.events.put(("CONS_STATE", state.get("consolidation_active"), state))
            pc = float(prev.get("consolidation_score") or 0.0)
            cc = float(state.get("consolidation_score") or 0.0)
            if cc >= CONS_THRESHOLD and pc < CONS_THRESHOLD:
                self.events.put(("CONS_SIGNAL", cc, state))
        self._prev = dict(state)

    def snapshot(self) -> Dict[str, Any]:
        return dict(self.last_state)

    def errors(self) -> List[Dict[str, Any]]:
        if self.feed is None:
            return []
        try:
            return self.feed.get_errors()
        except Exception:
            return []

    def clear_errors(self) -> None:
        if self.feed is None:
            return
        try:
            self.feed.clear_errors()
        except Exception:
            pass

    def bar_count(self) -> int:
        if self.feed is None:
            return 0
        return self.feed.bar_count


_controller: Optional[FeedController] = None


def _format_event(kind: str, value: Any, interval: str) -> str:
    head = f"<b>XAUUSD · {interval}</b>\n"
    if kind == "TREND":
        return head + f"📈 Trend changed → <b>{value}</b>"
    if kind == "BOS":
        return head + f"⚡ Break of Structure → <b>{value}</b>"
    if kind == "CHOCH":
        return head + f"🔄 Change of Character → <b>{value}</b>"
    if kind == "SWEEP":
        return head + f"💧 Liquidity Sweep → <b>{value}</b>"
    if kind == "CONS_STATE":
        return head + ("🔒 Consolidation ACTIVE" if value else "🔓 Consolidation ended (breakout)")
    if kind == "CONS_SIGNAL":
        return head + f"🚨 Consolidation starting — score <b>{_score(value)}</b>"
    return head + f"{kind}: {value}"


def _format_state(state: Dict[str, Any], interval: str, bars: int) -> str:
    lines = [
        f"<b>XAUUSD · {interval}</b>",
        f"Bars: {bars}",
        f"Trend: <b>{state.get('trend') or 'UNDEFINED'}</b>",
        f"BOS: {state.get('bos_direction') or '—'}",
        f"CHoCH: {state.get('choch_direction') or '—'}",
        f"Liquidity Sweep: {state.get('liquidity_sweep') or '—'}",
        f"Displacement: {state.get('displacement') or '—'}",
        f"Premium / Discount: {state.get('premium_discount') or '—'}",
        f"Last Swing High: {_price(state.get('last_swing_high'))}",
        f"Last Swing Low: {_price(state.get('last_swing_low'))}",
        f"Consolidation Score: <b>{_score(state.get('consolidation_score'))}</b>",
        f"Consolidation Active: {'YES' if state.get('consolidation_active') else 'no'}",
    ]
    if state.get("consolidation_active"):
        lines.append(f"Box Resistance: {_price(state.get('consolidation_resistance'))}")
        lines.append(f"Box Support: {_price(state.get('consolidation_support'))}")
    lines.append(f"ATR: {_price(state.get('last_atr'))}")
    return "\n".join(lines)


def _tf_keyboard(current: str) -> InlineKeyboardMarkup:
    rows = []
    row = []
    for iv in INTERVALS_ORDER:
        label = f"• {iv}" if iv == current else iv
        row.append(InlineKeyboardButton(label, callback_data=f"tf:{iv}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def _check_auth(update: Update) -> bool:
    if _authorized(update):
        return True
    chat = update.effective_chat
    if chat is not None:
        try:
            await chat.send_message("Not authorized.")
        except TelegramError:
            pass
    return False


async def cmd_whoami(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None:
        return
    await update.message.reply_text(f"Your chat ID: <code>{chat.id}</code>", parse_mode=ParseMode.HTML)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    ctrl = _controller
    if ctrl is None:
        await update.message.reply_text("Engine not ready.")
        return
    ctrl.subscribers.add(update.effective_chat.id)
    text = (
        "🧠 <b>XAUUSD ICT Structure Bot</b>\n\n"
        "Subscribed to live updates.\n\n"
        "/status — full snapshot\n"
        "/trend — trend only\n"
        "/consolidation — consolidation info\n"
        "/tf — pick timeframe\n"
        "/errors — recent errors\n"
        "/clearerrors — wipe error log\n"
        "/stop — unsubscribe"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    ctrl = _controller
    if ctrl is None:
        return
    ctrl.subscribers.discard(update.effective_chat.id)
    await update.message.reply_text("🔕 Unsubscribed.")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    ctrl = _controller
    if ctrl is None:
        await update.message.reply_text("Engine not ready.")
        return
    await update.message.reply_text(
        _format_state(ctrl.snapshot(), ctrl.interval, ctrl.bar_count()),
        parse_mode=ParseMode.HTML,
    )


async def cmd_trend(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    ctrl = _controller
    if ctrl is None:
        return
    state = ctrl.snapshot()
    await update.message.reply_text(
        f"Trend on {ctrl.interval}: <b>{state.get('trend') or 'UNDEFINED'}</b>",
        parse_mode=ParseMode.HTML,
    )


async def cmd_consolidation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    ctrl = _controller
    if ctrl is None:
        return
    state = ctrl.snapshot()
    active = bool(state.get("consolidation_active"))
    lines = [
        f"Consolidation Score: <b>{_score(state.get('consolidation_score'))}</b>",
        f"Active: {'YES' if active else 'no'}",
    ]
    if active:
        lines.append(f"Resistance: {_price(state.get('consolidation_resistance'))}")
        lines.append(f"Support: {_price(state.get('consolidation_support'))}")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def _apply_tf(update: Update, ctrl: FeedController, interval: str) -> None:
    q = update.callback_query
    if interval == ctrl.interval:
        text = f"Already on <b>{interval}</b>."
        if q:
            await q.answer()
            await q.edit_message_text(text, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        return
    if q:
        await q.answer(f"Switching to {interval}...")
        await q.edit_message_text(
            f"⏳ Switching to <b>{interval}</b> and reloading history...",
            parse_mode=ParseMode.HTML,
        )
    else:
        await update.message.reply_text(
            f"⏳ Switching to <b>{interval}</b> and reloading history...",
            parse_mode=ParseMode.HTML,
        )
    try:
        await asyncio.to_thread(ctrl.switch, interval)
    except Exception as e:
        logger.exception("switch failed")
        done = f"❌ Failed to switch: {e}"
    else:
        done = f"✅ Timeframe is now <b>{interval}</b>. Bars: {ctrl.bar_count()}."
    try:
        if q:
            await q.edit_message_text(done, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(done, parse_mode=ParseMode.HTML)
    except TelegramError:
        pass


async def cmd_tf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    ctrl = _controller
    if ctrl is None:
        return
    args = context.args or []
    if args:
        iv = args[0].lower()
        if iv not in GoldDataFeed.VALID_INTERVALS:
            await update.message.reply_text(
                f"Invalid timeframe. Use one of: {', '.join(INTERVALS_ORDER)}"
            )
            return
        await _apply_tf(update, ctrl, iv)
        return
    await update.message.reply_text(
        f"Current: <b>{ctrl.interval}</b>\nPick a timeframe:",
        reply_markup=_tf_keyboard(ctrl.interval),
        parse_mode=ParseMode.HTML,
    )


async def on_tf_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    q = update.callback_query
    if q is None:
        return
    data = q.data or ""
    if not data.startswith("tf:"):
        return
    iv = data.split(":", 1)[1]
    ctrl = _controller
    if ctrl is None:
        await q.answer("Engine not ready.")
        return
    if iv not in GoldDataFeed.VALID_INTERVALS:
        await q.answer("Invalid timeframe.")
        return
    await _apply_tf(update, ctrl, iv)


async def cmd_errors(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    ctrl = _controller
    if ctrl is None:
        return
    errs = ctrl.errors()
    if not errs:
        await update.message.reply_text("✅ No errors logged.")
        return
    lines = ["⚠️ <b>Recent errors</b>"]
    for e in errs[-10:]:
        lines.append(
            f"<code>{e.get('time','')}</code>\n"
            f"<b>{e.get('kind','?')}</b>: {e.get('message','')}"
        )
    await update.message.reply_text("\n\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_clearerrors(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    ctrl = _controller
    if ctrl is None:
        return
    ctrl.clear_errors()
    await update.message.reply_text("🧹 Error log cleared.")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _check_auth(update):
        return
    text = (
        "🧠 <b>Commands</b>\n"
        "/start — subscribe\n"
        "/stop — unsubscribe\n"
        "/status — full state\n"
        "/trend — trend only\n"
        "/consolidation — consolidation info\n"
        "/tf [interval] — 1m,5m,15m,30m,1h,4h,1d\n"
        "/errors — last 10 errors\n"
        "/clearerrors — wipe error log\n"
        "/whoami — show your chat ID"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def _drain_events(context: ContextTypes.DEFAULT_TYPE) -> None:
    ctrl = _controller
    if ctrl is None:
        return
    if not ctrl.subscribers:
        while True:
            try:
                ctrl.events.get_nowait()
            except Empty:
                return
    while True:
        try:
            kind, value, state = ctrl.events.get_nowait()
        except Empty:
            return
        text = _format_event(kind, value, ctrl.interval)
        for chat_id in list(ctrl.subscribers):
            try:
                await context.bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)
            except TelegramError:
                logger.exception("send failed")
            except Exception:
                logger.exception("send failed")


def main() -> None:
    global _controller
    _controller = FeedController(max_bars=MAX_BARS, interval=DEFAULT_INTERVAL)
    _controller.start()

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("trend", cmd_trend))
    app.add_handler(CommandHandler("consolidation", cmd_consolidation))
    app.add_handler(CommandHandler("tf", cmd_tf))
    app.add_handler(CommandHandler("errors", cmd_errors))
    app.add_handler(CommandHandler("clearerrors", cmd_clearerrors))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("whoami", cmd_whoami))
    app.add_handler(CallbackQueryHandler(on_tf_button, pattern=r"^tf:"))

    if app.job_queue is not None:
        app.job_queue.run_repeating(_drain_events, interval=1.0, first=2.0)

    logger.info("Bot starting | interval=%s | max_bars=%d", DEFAULT_INTERVAL, MAX_BARS)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
