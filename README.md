# Chinese checkers

## Introdution

Chinese checkers is a strategy board game for 2–6 players. The board is a six-pointed star with small holes arranged in triangular regions. Each player controls a set of colored pieces (usually marbles). The goal is to move all your pieces from your starting triangle to the opposite triangle before your opponents do. On each turn, a piece can: Move to an adjacent empty hole, or jump over another piece (your own or an opponent’s) into an empty hole. Multiple jumps can be chained in one turn.

I love playing Chinese checkers with my family when I'm young. Generally, there were three playing methods in my family:

+ Two players start from two opposite angles;
+ Two players and each of them has three colors, where each color of piece is opposite of one of the opponent's colors;
+ Three or more players compete together.

<div style="display: flex; justify-content: center; gap: 10px;">
    <img src="./assets/1.jpg", width="30%">
    <img src="./assets/2.jpg", width="30%">
    <img src="./assets/3.jpg", width="30%">
</div>

In this repository, I would gradually complete these three playing methods and offer a pretty UI interface. Meanwhile, I'l provide some bots with different searching algorithms. In the future, I also want to try some RL algorithms in multi-agents tasks. Coming soon ~~~

## Getting started

Nothing to install — the game runs on the Python standard library alone, and Tkinter
ships with CPython. Pillow is optional: when it is available the marbles are drawn as
anti-aliased sprites instead of stacked ovals.

```bash
python3 -m chinese_checkers                    # launch the game
```

Two modes on the start screen:

+ **经典对战** — 2 to 6 players, one colour each, racing into the opposite corner.
+ **三色对战** (playing method 2) — two players with **three colours each**. Every colour
  has to reach its own opposite corner, so one player runs 橙/黄/红 into the corners the
  other starts in and vice versa. The two simply alternate turns, one piece per turn,
  and you only win once all three of your colours are home.

### Playing against the computer

Whenever a table seats exactly **two players** — classic 2-player or 三色对战 — each
player row on the start screen gets a 人类 / 电脑 switch, plus one difficulty for the
table. Flip either side (or both) and press 开始游戏; the computer moves on its own
whenever the turn is its.

| 难度 | What it does | Thinking time |
| --- | --- | --- |
| 简单 | One move ahead, picking loosely among the decent replies | instant |
| 普通 | Fixed three-ply search | ~50 ms |
| 困难 | Iterative deepening, typically 5–6 plies in the midgame | ~1 s |

It is a **minimax search with alpha-beta pruning**, iterative deepening, a Zobrist
transposition table and killer-move ordering; positions are scored by how far every
marble still has to travel to the tip of the camp it must fill. See
`chinese_checkers/agents/` — and `docs/worklog/2026-08-03.md` for why alpha-beta rather
than MCTS. The search runs on a worker thread over its own copy of the position, so the
window stays responsive, and 悔棋 takes back the computer's reply along with your own
move so the turn comes back to you.

Benchmark two bots against each other with:

```bash
python3 scripts/bench_agent.py --a hard --b normal --games 6
```

Then play:

| Action | How |
| --- | --- |
| Select a piece | Click one of your glowing marbles |
| Build a route | Click a highlighted cell — dots are single steps, rings are jumps; keep clicking to chain jumps |
| Confirm the move | Click the current route endpoint again, `Enter`, or the 确认走子 button |
| Undo one hop of the route | `Backspace` |
| Deselect | `Esc` |
| Take back the last move | `Cmd/Ctrl+Z` |
| Cycle your movable pieces | `Tab` |

Illegal cells are never highlighted and clicking one changes nothing, so an illegal move simply cannot be played. When a player gets every piece home they are ranked and
the rest play on; finished players are skipped automatically.

Rules implemented: classic adjacent jumps (chained, may turn, never mixed with a
single step); a piece that has entered its target triangle may not leave it; and a
player wins by filling every hole of the target triangle that an opponent is not
squatting in, so blocking cannot deadlock the game.
