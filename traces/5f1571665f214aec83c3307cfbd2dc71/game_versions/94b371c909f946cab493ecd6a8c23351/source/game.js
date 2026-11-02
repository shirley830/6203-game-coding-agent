/* Treasure Grid - game logic, rendering, and test contract */
(function () {
  'use strict';

  var COLS = 5, ROWS = 5;
  var CELL = 80;

  var INITIAL = {
    player: { col: 1, row: 1 },
    treasures: [
      { col: 5, row: 1 },
      { col: 3, row: 3 },
      { col: 1, row: 5 }
    ],
    walls: [
      { col: 2, row: 2 },
      { col: 3, row: 2 },
      { col: 4, row: 4 }
    ],
    trap: { col: 5, row: 3 },
    exit: { col: 5, row: 5 }
  };

  var state;

  function resetState() {
    state = {
      player: { col: INITIAL.player.col, row: INITIAL.player.row },
      treasures: INITIAL.treasures.map(function (t) { return { col: t.col, row: t.row }; }),
      walls: INITIAL.walls,
      trap: INITIAL.trap,
      exit: INITIAL.exit,
      score: 0,
      status: 'playing'
    };
  }

  function isWall(col, row) {
    return state.walls.some(function (w) { return w.col === col && w.row === row; });
  }

  function treasureAt(col, row) {
    for (var i = 0; i < state.treasures.length; i++) {
      if (state.treasures[i].col === col && state.treasures[i].row === row) return i;
    }
    return -1;
  }

  function tryMove(dcol, drow) {
    if (state.status !== 'playing') return;
    var col = state.player.col + dcol;
    var row = state.player.row + drow;
    if (col < 1 || col > COLS || row < 1 || row > ROWS) return;
    if (isWall(col, row)) return;

    state.player.col = col;
    state.player.row = row;

    var ti = treasureAt(col, row);
    if (ti !== -1) {
      state.treasures.splice(ti, 1);
      state.score += 1;
    }

    if (col === state.trap.col && row === state.trap.row) {
      state.status = 'lost';
    } else if (col === state.exit.col && row === state.exit.row && state.treasures.length === 0) {
      state.status = 'won';
    }

    render();
  }

  /* ---------- Rendering ---------- */

  var canvas = document.getElementById('board');
  var ctx = canvas.getContext('2d');
  var scoreEl = document.getElementById('score');
  var statusEl = document.getElementById('status');

  function drawCell(col, row, fill) {
    var x = (col - 1) * CELL, y = (row - 1) * CELL;
    ctx.fillStyle = fill;
    ctx.fillRect(x + 2, y + 2, CELL - 4, CELL - 4);
  }

  function render() {
    // floor
    ctx.fillStyle = '#10161f';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    for (var r = 1; r <= ROWS; r++) {
      for (var c = 1; c <= COLS; c++) {
        drawCell(c, r, (r + c) % 2 === 0 ? '#1c2635' : '#182130');
      }
    }
    // grid lines
    ctx.strokeStyle = '#2c3a52';
    ctx.lineWidth = 1;
    for (var i = 0; i <= COLS; i++) {
      ctx.beginPath(); ctx.moveTo(i * CELL, 0); ctx.lineTo(i * CELL, ROWS * CELL); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(0, i * CELL); ctx.lineTo(COLS * CELL, i * CELL); ctx.stroke();
    }
    // exit
    drawCell(state.exit.col, state.exit.row, '#3ecf6a');
    ctx.fillStyle = '#0c3d1d';
    ctx.font = 'bold 14px Arial';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText('EXIT', (state.exit.col - 0.5) * CELL, (state.exit.row - 0.5) * CELL);
    // trap
    drawCell(state.trap.col, state.trap.row, '#e04545');
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 20px Arial';
    ctx.fillText('X', (state.trap.col - 0.5) * CELL, (state.trap.row - 0.5) * CELL);
    // walls
    state.walls.forEach(function (w) {
      drawCell(w.col, w.row, '#5a6675');
      ctx.strokeStyle = '#39424e';
      ctx.lineWidth = 2;
      ctx.strokeRect((w.col - 1) * CELL + 10, (w.row - 1) * CELL + 10, CELL - 20, CELL - 20);
    });
    // treasures
    state.treasures.forEach(function (t) {
      var cx = (t.col - 0.5) * CELL, cy = (t.row - 0.5) * CELL;
      ctx.fillStyle = '#ffd75e';
      ctx.beginPath();
      ctx.moveTo(cx, cy - 20);
      ctx.lineTo(cx + 18, cy);
      ctx.lineTo(cx, cy + 20);
      ctx.lineTo(cx - 18, cy);
      ctx.closePath();
      ctx.fill();
      ctx.strokeStyle = '#a8841b';
      ctx.lineWidth = 2;
      ctx.stroke();
    });
    // player
    var px = (state.player.col - 0.5) * CELL, py = (state.player.row - 0.5) * CELL;
    ctx.fillStyle = '#4aa3ff';
    ctx.beginPath();
    ctx.arc(px, py, 22, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = '#1c5c9e';
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.fillStyle = '#fff';
    ctx.beginPath();
    ctx.arc(px, py - 4, 5, 0, Math.PI * 2);
    ctx.fill();

    // HUD
    scoreEl.textContent = 'Score: ' + state.score;
    statusEl.className = '';
    if (state.status === 'won') {
      statusEl.textContent = 'You Win!';
      statusEl.className = 'won';
    } else if (state.status === 'lost') {
      statusEl.textContent = 'Game Over';
      statusEl.className = 'lost';
    } else {
      statusEl.textContent = 'Playing';
    }
  }

  /* ---------- Input ---------- */

  var KEYS = {
    ArrowUp: [0, -1],
    ArrowDown: [0, 1],
    ArrowLeft: [-1, 0],
    ArrowRight: [1, 0]
  };

  window.addEventListener('keydown', function (e) {
    if (KEYS[e.key]) {
      e.preventDefault();
      tryMove(KEYS[e.key][0], KEYS[e.key][1]);
    }
  });

  document.getElementById('restart').addEventListener('click', function () {
    resetState();
    render();
  });

  /* ---------- Test contract ---------- */

  Object.defineProperty(window, '__gameState', {
    get: function () {
      return {
        grid: { cols: COLS, rows: ROWS },
        player: { col: state.player.col, row: state.player.row },
        score: state.score,
        status: state.status,
        treasuresRemaining: state.treasures.length
      };
    }
  });

  window.__gameTest = {
    placePlayer: function (col, row) {
      // Teleport to an open floor cell, keep playing (precondition only).
      if (col < 1 || col > COLS || row < 1 || row > ROWS) return;
      if (isWall(col, row)) return;
      state.player.col = col;
      state.player.row = row;
      render();
    },
    collectAllTreasures: function () {
      // Remove all remaining treasures via the real scoring rule.
      state.score += state.treasures.length;
      state.treasures = [];
      render();
    }
  };

  resetState();
  render();
})();
