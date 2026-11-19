"""Native desktop (Qt) front-end of APRSBox.

The web panel (FastAPI + Jinja templates) is replaced by a real application
window.  The APRS runtime that used to run as the separate ``aprsbox-core``
service runs in a background thread of the same process (see ``runtime``),
while the window talks to the very same service layer the web routes used.
"""
