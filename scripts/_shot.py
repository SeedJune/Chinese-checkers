"""Screenshot a Tk window reliably on macOS.

Pillow's ``ImageGrab.grab(bbox=...)`` mixes up points and pixels on Retina
displays and happily returns whatever window happens to sit at those screen
coordinates -- usually the editor rather than our own window.  Shelling out to
the system ``screencapture`` with an explicit region avoids both problems:
``-R`` takes points (the same units Tk reports) and captures the screen
contents, so raising our window first is enough to guarantee we photograph it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from pathlib import Path


def activate_self() -> None:
    """Pull our own Tk window in front of whatever else is on screen.

    A Python process launched from a terminal is not a foreground GUI app, so
    Tk's own ``lift``/``-topmost`` leaves the window buried behind the editor
    and we would screenshot that instead.  Asking System Events to make our
    pid frontmost is the one thing that reliably works.
    """
    script = (
        f'tell application "System Events" to set frontmost of '
        f"(first process whose unix id is {os.getpid()}) to true"
    )
    subprocess.run(["osascript", "-e", script], check=False, capture_output=True)


def grab_window(root: tk.Misc, path: str | Path) -> bool:
    """Capture exactly ``root``'s bounds to ``path``.  Returns success."""
    root.update_idletasks()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["screencapture", "-x", "-o", "-R", f"{x},{y},{w},{h}", str(path)],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"截图失败: {exc}", file=sys.stderr)
        return False
    print(f"已保存截图: {path}")
    return True


def shoot_and_quit(root: tk.Tk, path: str | Path, delay: int = 1400) -> None:
    """Bring ``root`` to the front, screenshot it after ``delay`` ms, then quit."""
    root.lift()
    root.attributes("-topmost", True)

    # Activate from inside the event loop, not before it: raising the window
    # before Tk has finished mapping it lets whatever was frontmost take the
    # screen back, and we photograph that instead.
    def raise_then_snap() -> None:
        root.lift()
        activate_self()
        root.update()
        root.after(delay, lambda: (grab_window(root, path), root.destroy()))

    root.after(250, raise_then_snap)
