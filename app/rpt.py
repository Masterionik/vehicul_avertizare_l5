"""RPT: 60 s status report (counts, max level, impacts, sensor faults, TX/RX per radio)."""

RPT_RADIOS = ("zigbee", "mesh")


class RPT:
    def __init__(self):
        self.reset()

    def reset(self):
        self._samples = 0
        self._valid = 0
        self._max_level = 0
        self._min_distance = None
        self._impacts = 0
        self._faults = 0
        self._tx = dict.fromkeys(RPT_RADIOS, 0)
        self._rx = dict.fromkeys(RPT_RADIOS, 0)

    def add_sample(self, distance_cm, level, valid):
        self._samples += 1
        self._max_level = max(self._max_level, level)
        if valid:
            self._valid += 1
            if self._min_distance is None or distance_cm < self._min_distance:
                self._min_distance = distance_cm

    def add_impact(self):
        self._impacts += 1

    def add_fault(self):
        self._faults += 1

    def add_tx(self, radio):
        if radio in self._tx:
            self._tx[radio] += 1

    def add_rx(self, radio):
        if radio in self._rx:
            self._rx[radio] += 1

    def snapshot(self, reset=False):
        """Return the report dict; with ``reset=True`` start a new period afterwards."""
        report = {
            "samples": self._samples,
            "valid_samples": self._valid,
            "max_level": self._max_level,
            "min_distance_cm": self._min_distance,
            "impacts": self._impacts,
            "sensor_faults": self._faults,
            "tx": dict(self._tx),
            "rx": dict(self._rx),
        }
        if reset:
            self.reset()
        return report
