"""Self-play bench for the search bots -- how strong, how fast, how deep.

This is the tool the evaluation weights in ``agents/evaluate.py`` are tuned
with: change a weight, run a few dozen games, look at the win rate rather than
at one game that felt convincing.  Sides are swapped every other game, because
in a race moving first is worth something and a one-sided match-up would
measure that instead of the bots.

Usage::

    python3 scripts/bench_agent.py                      # normal vs random, 10 games
    python3 scripts/bench_agent.py --a hard --b normal --games 6
    python3 scripts/bench_agent.py --a normal --b easy --triple
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chinese_checkers.agents import LEVELS, RandomAgent, make_agent  # noqa: E402
from chinese_checkers.agents.minimax import MinimaxAgent  # noqa: E402
from chinese_checkers.core.game import Game  # noqa: E402

#: ``--a``/``--b`` accept the menu's difficulty keys plus a random baseline.
KINDS: tuple[str, ...] = tuple(key for key, _label in LEVELS) + ("random",)


def build(kind: str, seed: int):
    return RandomAgent(seed=seed) if kind == "random" else make_agent(kind, seed=seed)


class Trace:
    """Per-move bookkeeping for one agent across a whole match."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.times: list[float] = []
        self.nodes: list[int] = []
        self.depths: list[int] = []
        self.wins = 0

    def record(self, agent, elapsed: float) -> None:
        self.times.append(elapsed)
        if isinstance(agent, MinimaxAgent):
            self.nodes.append(agent.stats.nodes)
            self.depths.append(agent.stats.depth)

    def report(self, games: int) -> str:
        line = f"  {self.kind:<8} 胜 {self.wins}/{games}"
        if self.times:
            line += f"  平均 {statistics.mean(self.times) * 1000:6.1f} ms/手"
            line += f"  最慢 {max(self.times) * 1000:6.1f} ms"
        if self.nodes:
            total_time = sum(self.times) or 1e-9
            line += f"  {sum(self.nodes) / total_time:8.0f} 结点/秒"
        if self.depths:
            line += f"  深度 {min(self.depths)}-{max(self.depths)}"
        return line


def play(agents: dict[int, object], traces: dict[int, Trace], colors_each: int, limit: int):
    """One game to the finish (or to ``limit`` plies).  Returns the game."""
    game = Game.new(2, colors_each=colors_each)
    for _ in range(limit):
        if game.is_over:
            break
        player = game.state.current
        agent = agents[player]
        started = time.monotonic()
        move = agent.select_move(game)
        traces[player].record(agent, time.monotonic() - started)
        # Through the builder rather than straight onto the state, so the bench
        # walks the same path the UI does and would notice if that path broke.
        game.select(move.origin)
        for cell in move.path:
            game.extend(cell)
        game.confirm()
    return game


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--a", default="normal", choices=KINDS, help="第一个 bot")
    parser.add_argument("--b", default="random", choices=KINDS, help="第二个 bot")
    parser.add_argument("--games", type=int, default=10)
    parser.add_argument("--limit", type=int, default=600, help="每局手数上限")
    parser.add_argument("--triple", action="store_true", help="用三色对战玩法")
    args = parser.parse_args()

    colors_each = 3 if args.triple else 1
    traces = {"a": Trace(args.a), "b": Trace(args.b)}
    lengths: list[int] = []
    unfinished = 0

    mode = "三色对战" if args.triple else "经典 2 人"
    print(f"{args.a} vs {args.b} · {args.games} 局 · {mode}\n")
    for i in range(args.games):
        # Swap who starts every other game: in a race the first move is worth
        # real tempo, so a fixed seating would measure that and not the bots.
        first = "a" if i % 2 == 0 else "b"
        second = "b" if first == "a" else "a"
        agents = {0: build(traces[first].kind, i), 1: build(traces[second].kind, 100 + i)}
        seat_of = {first: 0, second: 1}

        started = time.monotonic()
        game = play(agents, {0: traces[first], 1: traces[second]}, colors_each, args.limit)
        lengths.append(len(game.state.history))

        if game.state.rankings:
            winner = game.state.rankings[0]
            key = first if seat_of[first] == winner else second
            traces[key].wins += 1
            home = game.state.pieces_home(winner)
            total = game.state.pieces_total(winner)
            result = f"{traces[key].kind} 胜（到家 {home}/{total}）"
        else:
            unfinished += 1
            result = "未分胜负（到达手数上限）"
        print(
            f"  第 {i + 1:>2} 局  先手 {traces[first].kind:<8} "
            f"{len(game.state.history):>3} 手  {time.monotonic() - started:5.1f}s  {result}"
        )

    print("\n结果:")
    for trace in traces.values():
        print(trace.report(args.games))
    print(f"\n  平均局长 {statistics.mean(lengths):.0f} 手", end="")
    if unfinished:
        print(f"  ·  {unfinished} 局未分胜负", end="")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
