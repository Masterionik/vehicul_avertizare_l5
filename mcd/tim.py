"""TIM driver: polling-based timing for SCHED (no interrupts).

Channels: 1 s (Zigbee), 60 s (status report), alert (expiry, checked by SCHED), 100 ms (node step).
Missed periods are resynchronized without bursts of calls.
"""
import threading
import time

TIM_CH_1S = 0
TIM_CH_60S = 1
TIM_CH_ALERT = 2
TIM_CH_100MS = 3
TIM_NUM_CHANNELS = 4

TIM_MODE_PERIODIC = 0
TIM_MODE_ONESHOT = 1

_lock = threading.RLock()
_clock = time.monotonic      # seconds, monotonic
_timers = {}                 # channel -> {"period", "mode", "deadline"}


def _now_ms():
    return int(_clock() * 1000)


def _check_channel(channel):
    if (not isinstance(channel, int) or isinstance(channel, bool)
            or not 0 <= channel < TIM_NUM_CHANNELS):
        raise ValueError("invalid TIM channel: %r" % (channel,))


def TIM_vInit():
    """Stop all channels and switch back to the real clock."""
    global _clock
    with _lock:
        _clock = time.monotonic
        _timers.clear()


def TIM_vSetClock(clock_fn):
    """Replace the time source (seconds); used in tests."""
    global _clock
    with _lock:
        _clock = clock_fn


def TIM_u32GetMs():
    """Milliseconds since an arbitrary origin, 32 bit (wraps at 2^32)."""
    return _now_ms() & 0xFFFFFFFF


def TIM_vStart(channel, period_ms, mode=TIM_MODE_PERIODIC):
    _check_channel(channel)
    if not isinstance(period_ms, int) or isinstance(period_ms, bool) or period_ms < 1:
        raise ValueError("invalid period: %r" % (period_ms,))
    if mode not in (TIM_MODE_PERIODIC, TIM_MODE_ONESHOT):
        raise ValueError("invalid mode: %r" % (mode,))
    with _lock:
        _timers[channel] = {"period": period_ms, "mode": mode,
                            "deadline": _now_ms() + period_ms}


def TIM_vStop(channel):
    _check_channel(channel)
    with _lock:
        _timers.pop(channel, None)


def TIM_bIsRunning(channel):
    _check_channel(channel)
    with _lock:
        return channel in _timers


def TIM_bHasElapsed(channel):
    """True once per elapsed period (consumes the event)."""
    _check_channel(channel)
    with _lock:
        tmr = _timers.get(channel)
        if tmr is None:
            return False
        now = _now_ms()
        if now < tmr["deadline"]:
            return False
        if tmr["mode"] == TIM_MODE_ONESHOT:
            del _timers[channel]
        else:
            missed = (now - tmr["deadline"]) // tmr["period"]
            tmr["deadline"] += (missed + 1) * tmr["period"]
        return True


def TIM_vDelayMs(ms):
    """Blocking delay."""
    if not isinstance(ms, (int, float)) or isinstance(ms, bool) or ms < 0:
        raise ValueError("invalid delay: %r" % (ms,))
    time.sleep(ms / 1000.0)
