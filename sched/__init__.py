"""SCHED: timing helpers on top of TIM (100 ms / 1 s / 60 s ticks, alert expiry).

Each tick function returns True once per elapsed period (the event is consumed).
Ticks are polled by ONE runtime thread; nothing here uses interrupts or threads.
"""

from mcd.tim import (TIM_CH_100MS, TIM_CH_1S, TIM_CH_60S, TIM_CH_ALERT, TIM_bHasElapsed,
                     TIM_vStart, TIM_vStop)

__all__ = [
    "TIM_CH_100MS",
    "TIM_CH_1S",
    "TIM_CH_60S",
    "TIM_CH_ALERT",
    "TIM_bHasElapsed",
    "TIM_vStart",
    "TIM_vStop",
    "SCHED_vStart",
    "SCHED_vStop",
    "SCHED_bTick100ms",
    "SCHED_bTick1s",
    "SCHED_bTick60s",
    "scheduler_tick",
    "alert_expired",
]

SCHED_PERIODS_MS = {TIM_CH_100MS: 100, TIM_CH_1S: 1000, TIM_CH_60S: 60000}


def SCHED_vStart():
    """(Re)start the 100 ms, 1 s and 60 s periodic timers."""
    for channel, period in SCHED_PERIODS_MS.items():
        TIM_vStart(channel, period)


def SCHED_vStop():
    """Stop the periodic timers."""
    for channel in SCHED_PERIODS_MS:
        TIM_vStop(channel)


def SCHED_bTick100ms():
    """True once per elapsed 100 ms period (False if SCHED is not started)."""
    return TIM_bHasElapsed(TIM_CH_100MS)


def SCHED_bTick1s():
    """True once per elapsed 1 s period."""
    return TIM_bHasElapsed(TIM_CH_1S)


def SCHED_bTick60s():
    """True once per elapsed 60 s period."""
    return TIM_bHasElapsed(TIM_CH_60S)


def scheduler_tick(channel):
    return TIM_bHasElapsed(channel)


def alert_expired(deadline_ms, now_ms):
    return now_ms >= deadline_ms
