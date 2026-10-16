const ctx = document.getElementById('board').getContext('2d');
const scoreEl = document.getElementById('score');
let player, coin, score;
function reset() { player = {x: 20, y: 20}; coin = {x: 60, y: 20}; score = 0; render(); }
function render() {
  ctx.fillStyle = '#111'; ctx.fillRect(0, 0, 320, 240);
  ctx.fillStyle = '#fc3'; ctx.fillRect(coin.x, coin.y, 12, 12);
  ctx.fillStyle = '#3c6'; ctx.fillRect(player.x, player.y, 16, 16);
  scoreEl.textContent = 'Score: ' + score;
}
const moves = {ArrowRight: [20, 0], ArrowLeft: [-20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20]};
window.addEventListener('keydown', (event) => {
  const step = moves[event.key];
  if (!step) return;
  player.x = Math.max(0, Math.min(304, player.x + step[0]));
  player.y = Math.max(0, Math.min(224, player.y + step[1]));
  if (Math.abs(player.x - coin.x) < 16 && Math.abs(player.y - coin.y) < 16) {
    score += 1; coin = {x: (coin.x + 100) % 300, y: (coin.y + 60) % 220};
  }
  render();
});
document.getElementById('restart').addEventListener('click', reset);
reset();
