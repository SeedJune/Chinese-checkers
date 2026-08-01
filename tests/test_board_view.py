"""Mouse-only commit affordance on the interactive board.

These tests deliberately exercise the event-routing method without opening a
Tk window.  The game still supplies the real move-builder state machine; only
the animation method is replaced by a tiny recorder.
"""

from __future__ import annotations

import unittest

from chinese_checkers.ui.board_view import BoardView

from . import make_game
from .test_game import CHAIN_PIECES, FIRST_LANDING, ORIGIN


class EndpointConfirmationTests(unittest.TestCase):
    def test_clicking_current_landing_confirms_a_complete_route(self) -> None:
        game = make_game(CHAIN_PIECES)
        game.select(ORIGIN)
        game.extend(FIRST_LANDING)

        view = object.__new__(BoardView)
        view.game = game
        statuses: list[tuple[str, str]] = []
        view.on_status = lambda text, kind: statuses.append((text, kind))
        confirmed: list[bool] = []
        view.confirm_move = lambda: confirmed.append(True)

        self.assertTrue(view._click_while_building(FIRST_LANDING))
        self.assertEqual(confirmed, [True])
        self.assertEqual(statuses[-1], ("已确认路线，正在走子。", "success"))

