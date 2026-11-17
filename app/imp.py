"""IMP: impact detection from vibration edges.

An impact is declared when at least IMP_MIN_EDGES edges occur within IMP_WINDOW_MS. After a
declaration a lockout of IMP_LOCKOUT_MS ignores further edges. The impact flag stays 1 for
IMP_HOLD_MS so that ALR and the dashboard can see it.

``IMP`` is pure logic; ``IMP_vStep`` is the thin RTE wrapper.
"""

from rte import RTE_u16GetVibrationEdges, RTE_vWriteImpact

IMP_MIN_EDGES = 3
IMP_WINDOW_MS = 300
IMP_LOCKOUT_MS = 3000
IMP_HOLD_MS = 2000


class IMP:
    def __init__(self):
        self._events = []            # (time_ms, edge count)
        self._declared_at = None
        self.total = 0
        self.edges_total = 0

    def update(self, edges, now_ms):
        """Feed the edges counted since the previous call; True only at the moment of declaration."""
        self.edges_total += edges
        if self._declared_at is not None and now_ms - self._declared_at < IMP_LOCKOUT_MS:
            self._events.clear()
            return False
        if edges > 0:
            self._events.append((now_ms, edges))
        self._events = [(t, n) for t, n in self._events if now_ms - t <= IMP_WINDOW_MS]
        if sum(n for _, n in self._events) >= IMP_MIN_EDGES:
            self._events.clear()
            self._declared_at = now_ms
            self.total += 1
            return True
        return False

    def active(self, now_ms):
        """True while the impact flag is held."""
        return self._declared_at is not None and now_ms - self._declared_at < IMP_HOLD_MS


def IMP_vStep(imp, now_ms):
    """Read vibration edges through the RTE, update ``imp`` and publish the impact signal.

    Returns True when a new impact was declared in this step.
    """
    declared = imp.update(RTE_u16GetVibrationEdges(), now_ms)
    RTE_vWriteImpact(1 if imp.active(now_ms) else 0)
    return declared
