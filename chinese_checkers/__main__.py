"""``python3 -m chinese_checkers`` -- open the game window.

Kept to one line of real work so the package has a launcher without the UI
module having to know it is being run as a script.
"""

from __future__ import annotations

from .ui.app import run

if __name__ == "__main__":
    run()
