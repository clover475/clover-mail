# Contributing

This is an early macOS project. Small, focused improvements are welcome.

1. Open an issue describing the behavior and the Apple Mail/macOS version.
2. Use synthetic email fixtures only. Never attach a real message or credential.
3. Keep Apple Mail access read-only and isolate its schema in
   `src/clover_mail/apple_mail.py`.
4. Run `PYTHONPATH=src python3 -m unittest discover -s tests` before a pull
   request. CI runs the same synthetic suite on macOS and Linux.

New runtime dependencies should solve a concrete problem. Changes that add a
cloud data flow should document exactly what leaves the Mac in `docs/privacy.md`.
