# -*- coding: utf-8 -*-
"""Legacy native-pipeline verifier — disabled for foreground safety.

MaaFramework MessageInput posts WM_ACTIVATE before every Click/Scroll, which
raises Chrome_WidgetWin_0 (萝卜家园2). Use Background CustomAction tools that
call post_click_normalized instead. See docs/FOREGROUND_SAFE_INPUT.md.
"""

from __future__ import annotations

import sys


def main() -> int:
    print(
        "REFUSED: native pipeline Click/Scroll verification is disabled because "
        "Maa post_click activates the game window. Use post_click_normalized / "
        "Background UI tools. See docs/FOREGROUND_SAFE_INPUT.md.",
        flush=True,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
