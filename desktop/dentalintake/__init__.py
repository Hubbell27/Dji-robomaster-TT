"""Dental Intake for Windows: runs the intake server as a background service on
the office PC and opens the dashboard in its own window.

Everything patient-related lives in the shared backend package (``app``); this
package only adds what a desktop install needs: paths, config, certificates,
the service runner, backups/restore, the launcher window and the tray icon.
"""

__version__ = "1.0.0"
APP_NAME = "Dental Intake"
