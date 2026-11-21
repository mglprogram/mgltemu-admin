"""Ignore console Ctrl+C / Ctrl+Break for the headless fulfill chain.

WHY (2026-11-20, measured on the 04:00 round three times):
    an *external* console CTRL_C (exit 0xC000013A = 3221225786) hits every
    store subprocess 60-95 s after the round starts. All of them raise
    KeyboardInterrupt at once (auth probe sleep, subprocess._communicate,
    threading.start, os.replace) and that store is skipped for the day.
    `run_fulfill_daily_inner` already retries once, but the same blast hits
    the retry too, so BHY / BEL / BANGL / BE / KinglamanSport produced nothing
    on 11-19 and again on 11-20.

    The chain is headless: nobody is at the keyboard to mean "abort". So the
    right behaviour is to *ignore* Ctrl+C / Ctrl+Break and keep shipping.
    This is only about console signals - bounding a hung step still works,
    because the parent kills its child with `subprocess` timeout
    (TerminateProcess), which a signal handler cannot swallow.

HOW it is wired: `run_fulfill_daily_inner._run()` prepends the directory that
holds this file to PYTHONPATH, so every python process it spawns (and every
grandchild, since the env is inherited) imports this at startup via
`sitecustomize`.
"""

import signal

for _name in ("SIGINT", "SIGBREAK"):
    _sig = getattr(signal, _name, None)
    if _sig is None:
        continue
    try:
        signal.signal(_sig, signal.SIG_IGN)
    except (OSError, RuntimeError, ValueError):
        # not the main thread / no console: nothing to do
        pass
