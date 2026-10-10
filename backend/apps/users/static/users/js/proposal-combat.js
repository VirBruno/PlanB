(() => {
  const game = document.querySelector("[data-combat-game]");
  if (!game) return;

  const canvas = game.querySelector("[data-combat-canvas]");
  const context = canvas?.getContext("2d");
  const startButton = game.querySelector("[data-combat-start]");
  const flapButton = game.querySelector("[data-combat-flap]");
  const scoreLabel = game.querySelector("[data-combat-score]");
  const status = game.querySelector("[data-combat-status]");
  const saveForm = game.querySelector("[data-combat-save]");
  const scoreInput = game.querySelector("[data-combat-score-input]");
  if (!canvas || !context || !startButton || !flapButton || !saveForm || !scoreInput) return;

  const width = canvas.width;
  const height = canvas.height;
  const bird = { x: 150, y: 150, velocity: 0, radius: 13 };
  const pipes = [];
  let score = 0;
  let state = "idle";
  let previousTime = 0;
  let animationFrame = 0;

  const makePipe = (x) => ({
    x,
    gapY: 72 + Math.random() * (height - 144),
    passed: false,
  });

  const reset = () => {
    bird.y = height / 2;
    bird.velocity = 0;
    score = 0;
    pipes.length = 0;
    pipes.push(makePipe(width + 90));
    pipes.push(makePipe(width + 350));
    scoreLabel.textContent = "Puntaje: 0";
    saveForm.hidden = true;
    draw();
  };

  const draw = () => {
    context.clearRect(0, 0, width, height);
    context.fillStyle = "#dff3f2";
    context.fillRect(0, 0, width, height);
    context.fillStyle = "#ffffffa8";
    context.beginPath();
    context.ellipse(510, 74, 42, 13, 0, 0, Math.PI * 2);
    context.ellipse(540, 74, 25, 17, 0, 0, Math.PI * 2);
    context.fill();
    context.fillStyle = "#80b9a7";
    for (const pipe of pipes) {
      const gapTop = pipe.gapY - 57;
      const gapBottom = pipe.gapY + 57;
      context.fillRect(pipe.x, 0, 58, gapTop);
      context.fillRect(pipe.x - 5, gapTop - 12, 68, 12);
      context.fillRect(pipe.x, gapBottom, 58, height - gapBottom);
      context.fillRect(pipe.x - 5, gapBottom, 68, 12);
    }
    context.fillStyle = "#327c72";
    context.fillRect(0, height - 12, width, 12);
    context.fillStyle = "#ef8b54";
    context.beginPath();
    context.arc(bird.x, bird.y, bird.radius, 0, Math.PI * 2);
    context.fill();
    context.fillStyle = "#163f4b";
    context.beginPath();
    context.arc(bird.x + 5, bird.y - 3, 2, 0, Math.PI * 2);
    context.fill();
    if (state === "idle") {
      context.fillStyle = "#163f4b";
      context.font = "bold 18px sans-serif";
      context.textAlign = "center";
      context.fillText("Evitá los obstáculos", width / 2, 38);
    }
  };

  const endGame = () => {
    state = "over";
    startButton.disabled = false;
    flapButton.disabled = true;
    scoreInput.value = String(score);
    saveForm.hidden = false;
    status.textContent = `Partida terminada. Puntaje: ${score}. Guardá tu resultado para actualizar el leaderboard.`;
    draw();
  };

  const update = (delta) => {
    bird.velocity += 0.38 * delta;
    bird.y += bird.velocity * delta;
    for (const pipe of pipes) {
      pipe.x -= 2.4 * delta;
      if (!pipe.passed && pipe.x + 58 < bird.x) {
        pipe.passed = true;
        score += 1;
        scoreLabel.textContent = `Puntaje: ${score}`;
      }
      const gapTop = pipe.gapY - 57;
      const gapBottom = pipe.gapY + 57;
      const overlapsBird = bird.x + bird.radius > pipe.x && bird.x - bird.radius < pipe.x + 58;
      if (overlapsBird && (bird.y - bird.radius < gapTop || bird.y + bird.radius > gapBottom)) {
        return false;
      }
    }
    while (pipes.length && pipes[0].x < -70) pipes.shift();
    if (pipes.length < 2) pipes.push(makePipe(pipes[pipes.length - 1].x + 260));
    return bird.y - bird.radius > 0 && bird.y + bird.radius < height - 12;
  };

  const tick = (time) => {
    if (state !== "running") return;
    const delta = Math.min((time - previousTime) / 16.67 || 1, 2);
    previousTime = time;
    if (!update(delta)) {
      endGame();
      return;
    }
    draw();
    animationFrame = requestAnimationFrame(tick);
  };

  const flap = () => {
    if (state === "running") bird.velocity = -6.2;
  };

  startButton.addEventListener("click", () => {
    cancelAnimationFrame(animationFrame);
    reset();
    state = "running";
    startButton.disabled = true;
    flapButton.disabled = false;
    status.textContent = "Presioná Espacio, Volar o tocá el juego para esquivar.";
    previousTime = performance.now();
    animationFrame = requestAnimationFrame(tick);
  });
  flapButton.addEventListener("click", flap);
  canvas.addEventListener("pointerdown", flap);
  window.addEventListener("keydown", (event) => {
    if (state === "running" && (event.code === "Space" || event.code === "ArrowUp")) {
      event.preventDefault();
      flap();
    }
  });

  reset();
})();