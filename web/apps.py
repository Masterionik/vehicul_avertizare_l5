import atexit
import os
import signal
import sys

from django.apps import AppConfig

from .runtime import should_start_runtime


class WebConfig(AppConfig):
    name = "web"
    verbose_name = "Web dashboard"

    def ready(self):
        if not should_start_runtime(sys.argv, os.environ):
            return
        from app import node
        node.NODE_vStart()
        atexit.register(node.NODE_vStop)       # buzzer and LED are forced off when the process exits
        # systemd stops the service with SIGTERM, which would skip atexit: turn it into a normal exit.
        try:
            signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(0))
        except ValueError:                     # not the main thread (embedded server): keep atexit only
            pass
