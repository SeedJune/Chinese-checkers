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
