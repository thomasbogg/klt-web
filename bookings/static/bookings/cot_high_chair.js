const configEl = document.getElementById('cot-high-chair-pricing-config');
const config = configEl ? JSON.parse(configEl.textContent) : null;

const cotCheckbox = document.getElementById('cot_checkbox');
const highChairCheckbox = document.getElementById('high_chair_checkbox');
const priceValue = document.getElementById('cot-high-chair-price-value');

function computePrice() {
    if (!config || !priceValue) return;

    const isLongStay = config.nights > 7;
    let total = 0;
    if (cotCheckbox.checked) {
        total += parseFloat(isLongStay ? config.cot_long : config.cot_short);
    }
    if (highChairCheckbox.checked) {
        total += parseFloat(isLongStay ? config.high_chair_long : config.high_chair_short);
    }
    if (cotCheckbox.checked && highChairCheckbox.checked) {
        total -= parseFloat(config.combo_discount);
    }
    total = Math.max(total, 0);

    priceValue.textContent = (cotCheckbox.checked || highChairCheckbox.checked) ? `€${total.toFixed(2)}` : '–';
}

if (cotCheckbox && highChairCheckbox && priceValue) {
    cotCheckbox.addEventListener('change', computePrice);
    highChairCheckbox.addEventListener('change', computePrice);
    computePrice();
}
