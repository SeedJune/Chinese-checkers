"""Mouse-only commit affordance on the interactive board.

These tests deliberately exercise the event-routing method without opening a
Tk window.  The game still supplies the real move-builder state machine; only
the animation method is replaced by a tiny recorder.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

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


class LockedBoardTests(unittest.TestCase):
    """``locked`` is how the app hands the turn to a bot: the board keeps
    drawing, but it stops accepting and stops inviting input."""

    @staticmethod
    def _view(locked: bool) -> BoardView:
        view = object.__new__(BoardView)
        view.game = make_game(CHAIN_PIECES)
        view._animating = False
        view.locked = locked
        view._hover = None
        view._float = None
        return view

    def test_locked_board_offers_nothing_to_click(self) -> None:
        self.assertTrue(self._view(locked=False)._actionable())
        self.assertEqual(self._view(locked=True)._actionable(), frozenset())

    def test_locked_scene_drops_every_invitation(self) -> None:
        scene = self._view(locked=True).build_scene()
        self.assertEqual(scene.selectable, frozenset())
        self.assertEqual(scene.step_targets, frozenset())
        self.assertEqual(scene.jump_targets, frozenset())

    def test_a_click_on_a_locked_board_is_dropped_before_the_renderer(self) -> None:
        # Returning early is what keeps this safe to call without a renderer:
        # ``_on_click`` reaches for pixel geometry only past the guard.
        view = self._view(locked=True)
        before = dict(view.game.state.occupancy)
        view._on_click(SimpleNamespace(x=0, y=0))
        self.assertEqual(dict(view.game.state.occupancy), before)
        self.assertEqual(view.game.path, ())

