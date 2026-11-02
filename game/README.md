# Treasure Grid

A small browser game: navigate a 5x5 grid, collect all 3 treasures, avoid the trap, and reach the exit to win.

## Requirements

- Python 3 (used only to serve the files locally; no packages to install).
- No npm, no build step, no external or remote assets — everything runs locally.

## Installation

No dependencies need to be installed. Python 3's standard library is sufficient.

## Launch

From the `game/` directory run exactly:

```
python3 -m http.server 8000 --bind 127.0.0.1
```

Then open http://127.0.0.1:8000/ in a browser.

To stop the game, press Ctrl+C in the terminal running the server.

## How to Play

- Use the arrow keys (Up, Down, Left, Right) to move the blue player one cell per key press.
- Moving outside the board or into a grey wall has no effect.
- Collect the 3 yellow treasures; each one increases the score by 1.
- Stepping on the red trap cell (row 3, column 5) ends the game immediately: "Game Over".
- Reaching the green exit before collecting all 3 treasures does not end the game.
- Reaching the exit after collecting all 3 treasures wins the game: "You Win!".
- After winning or losing, movement is disabled.
- Click the "Restart" button at any time to restore the initial layout, score 0, and status "Playing".

## Legend

- Blue circle: Player (starts at row 1, column 1)
- Yellow diamond: Treasure (at (1,5), (3,3), (5,1))
- Grey square: Wall (at (2,2), (2,3), (4,4))
- Red cell with X: Trap (at (3,5))
- Green cell labeled EXIT: Exit (at (5,5))