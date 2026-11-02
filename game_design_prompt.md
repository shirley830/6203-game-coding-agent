# Game Specification: Treasure Grid

Build a playable browser game using HTML, CSS, and JavaScript.
The game must run locally without external services or downloaded assets.

## Board

Display a 5 × 5 grid. Coordinates are (row, column), starting at (1, 1)
in the top-left corner.

Initial positions:
- Player: (1, 1)
- Treasures: (1, 5), (3, 3), and (5, 1)
- Walls: (2, 2), (2, 3), and (4, 4)
- Trap: (3, 5)
- Exit: (5, 5)

Use distinct visual appearances for each type of cell.

## Controls

- Arrow keys move the player one cell per key press.
- Moving outside the board or into a wall has no effect.
- Arrow keys must not scroll the page.

## Rules

- The score starts at 0.
- Collecting a treasure increases the score by 1 and removes that treasure.
- Each treasure can be collected only once.
- Reaching the exit before collecting all three treasures does not end the game.
- Reaching the exit after collecting all three treasures wins the game.
- Entering the trap immediately loses the game.
- After winning or losing, movement is disabled.

## Interface

- Display the board, score, controls, and current game status.
- The initial status is "Playing".
- On victory, display "You Win!".
- On defeat, display "Game Over".
- Provide a visible "Restart" button at all times.
- Restart must restore every initial position, all treasures, score 0,
  and status "Playing".

## Delivery

Include a README with exact instructions for starting and playing the game.