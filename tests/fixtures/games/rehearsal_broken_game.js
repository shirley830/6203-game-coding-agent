const ctx = document.getElementById('board').getContext('2d');
let player = {x: 20, y: 20};
function draw() {
  ctx.fillStyle = '#111'; ctx.fillRect(0, 0, 320, 240);
  ctx.fillStyle = '#3c6'; ctx.fillRect(player.x, player.y, 16, 16);
}
draw();
