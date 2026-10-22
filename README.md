# Aircraft Watch

Terminal aircraft monitor using a ZIP-derived center point and configurable radius data stored in the shared location profile.

Run `python3 aircraft_ui.py`. The first interactive launch asks for a five-digit United States ZIP code. Change it later with `python3 aircraft_ui.py --set-zip [ZIP_CODE]`.

Run `python3 collect.py` to fetch nearby aircraft. This project uses only the Python standard library.

Generated SQLite history, logs, and local location settings are excluded from source control.
