"""Decides in which process the single NODE runtime thread starts (Django independent)."""

import os


def should_start_runtime(argv, environ):
    """True only in the process that serves requests.

    * ``manage.py runserver``: only the child of the autoreloader (RUN_MAIN=true) or with --noreload.
    * other ``manage.py`` commands (test, check, collectstatic, ...): never.
    * WSGI servers (gunicorn, mod_wsgi): yes. ``NODE_AUTOSTART=0`` disables it everywhere.
    """
    if environ.get("NODE_AUTOSTART", "1").strip() == "0":
        return False
    if argv and os.path.basename(argv[0]) == "manage.py":
        if len(argv) < 2 or argv[1] != "runserver":
            return False
        return environ.get("RUN_MAIN") == "true" or "--noreload" in argv
    return True
