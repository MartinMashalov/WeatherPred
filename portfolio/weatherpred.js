'use strict';
const belief = document.querySelector('#belief');
const ask = document.querySelector('#ask');
function updateEconomics() {
  const cents = Number(ask.value);
  const probability = Number(belief.value) / 100;
  // 100 * 0.07 * (cents/100) * (1-cents/100), integer numerator avoids a floating ceiling artifact.
  const feeCents = Math.ceil(7 * cents * (100 - cents) / 10000);
  const cost = (cents + feeCents) / 100;
  const conservativeProbability = Math.max(0, probability - 0.03);
  const edge = conservativeProbability - cost;
  const fraction = cost < 1 ? Math.max(0, 0.25 * edge / (1 - cost)) : 0;
  document.querySelector('#belief-value').textContent = belief.value + '%';
  document.querySelector('#ask-value').textContent = cents + '¢';
  document.querySelector('#total-cost').textContent = (cents + feeCents) + '¢';
  document.querySelector('#unit-edge').textContent = (edge >= 0 ? '+' : '−') + Math.abs(edge * 100).toFixed(0) + '¢';
  document.querySelector('#allocation').textContent = '$' + (100 * Math.min(0.05, fraction)).toFixed(2);
}
belief.addEventListener('input', updateEconomics);
ask.addEventListener('input', updateEconomics);
updateEconomics();
