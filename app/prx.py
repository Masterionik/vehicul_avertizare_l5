"""PRX: distance -> proximity alert level 0-3 with hysteresis and debounce.

Levels: 0 clear (> 150 cm), 1 caution (<= 150), 2 warning (<= 80), 3 danger (<= 30).
A level change is accepted only after PRX_CONFIRM_SAMPLES consecutive samples agree. Going to a
lower (less severe) level additionally needs PRX_HYSTERESIS_CM of margin above the threshold.
Invalid readings keep the previous level for PRX_INVALID_HOLD_MS, then the level falls back to 0
and the sensor-fault flag is raised until a valid reading arrives.

``PRX`` is pure logic (explicit time argument, no I/O); ``PRX_vStep`` is the thin RTE wrapper.
"""

from rte import RTE_DISTANCE_INVALID, RTE_u16GetDistanceCm, RTE_vWriteAlertLevel, RTE_vWriteDistanceCm

PRX_THRESHOLDS_CM = ((30, 3), (80, 2), (150, 1))   # (max distance, level), most severe first
PRX_CONFIRM_SAMPLES = 3
PRX_HYSTERESIS_CM = 5
PRX_INVALID_HOLD_MS = 1000


def PRX_u8LevelForDistance(distance_cm):
    """Raw level for a distance, without hysteresis or debounce (boundaries are inclusive)."""
    for limit, level in PRX_THRESHOLDS_CM:
        if distance_cm <= limit:
            return level
    return 0


class PRX:
    def __init__(self):
        self.level = 0
        self.fault = False
        self._pending = 0
        self._count = 0
        self._invalid_since = None

    def _candidate(self, distance_cm):
        raw = PRX_u8LevelForDistance(distance_cm)
        if raw >= self.level:
            return raw
        # Leaving the current level needs the distance to clear the threshold by the hysteresis.
        return min(self.level, PRX_u8LevelForDistance(distance_cm - PRX_HYSTERESIS_CM))

    def update(self, distance_cm, now_ms):
        """Feed one sample (``RTE_DISTANCE_INVALID`` for a bad reading); returns the current level."""
        if distance_cm == RTE_DISTANCE_INVALID:
            if self._invalid_since is None:
                self._invalid_since = now_ms
            if now_ms - self._invalid_since >= PRX_INVALID_HOLD_MS:
                self.level = 0
                self.fault = True
                self._pending, self._count = 0, 0
            return self.level
        self._invalid_since = None
        self.fault = False
        candidate = self._candidate(distance_cm)
        if candidate == self.level:
            self._pending, self._count = candidate, 0
        elif candidate == self._pending:
            self._count += 1
        else:
            self._pending, self._count = candidate, 1
        if candidate != self.level and self._count >= PRX_CONFIRM_SAMPLES:
            self.level = candidate
            self._pending, self._count = candidate, 0
        return self.level


def PRX_vStep(prx, now_ms):
    """Read the sensor through the RTE, update ``prx`` and publish distance and level signals."""
    distance = RTE_u16GetDistanceCm()
    level = prx.update(distance, now_ms)
    RTE_vWriteDistanceCm(distance)
    RTE_vWriteAlertLevel(level)
    return level
