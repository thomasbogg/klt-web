// Live running total across every Extras section (Welcome Pack, Cot & High Chair, Late Checkout,
// Mid-stay Clean, Airport Transfers, RequestType rows) - manage_extras.html only. Deliberately recomputes from
// scratch on any relevant DOM change rather than listening to each existing script's own output,
// so it stays correct regardless of dynamically added/removed transfer rows and doesn't need to
// coordinate load order with cot_high_chair.js/airport_transfers.js. The cot/high-chair and
// transfer formulas are duplicated from those two files (and ultimately from
// ExtrasSettings.compute_cot_high_chair_price()/compute_transfer_price() server-side) - keep in
// sync if either pricing rule changes.

const totalValue = document.getElementById('extras-total-value');

function flatPrice(checkboxId) {
    const checkbox = document.getElementById(checkboxId);
    if (!checkbox || !checkbox.checked) return 0;
    return parseFloat(checkbox.dataset.price) || 0;
}

function cotHighChairPrice() {
    const configEl = document.getElementById('cot-high-chair-pricing-config');
    const cotCheckbox = document.getElementById('cot_checkbox');
    const highChairCheckbox = document.getElementById('high_chair_checkbox');
    if (!configEl || !cotCheckbox || !highChairCheckbox) return 0;
    if (!cotCheckbox.checked && !highChairCheckbox.checked) return 0;

    const config = JSON.parse(configEl.textContent);
    const isLongStay = config.nights > 7;
    let total = 0;
    if (cotCheckbox.checked) total += parseFloat(isLongStay ? config.cot_long : config.cot_short);
    if (highChairCheckbox.checked) total += parseFloat(isLongStay ? config.high_chair_long : config.high_chair_short);
    if (cotCheckbox.checked && highChairCheckbox.checked) total -= total * (parseFloat(config.combo_discount_percent) / 100);
    return Math.max(total, 0);
}

function isNightTime(config, timeValue) {
    if (!timeValue) return false;
    const { night_start: start, night_end: end } = config;
    if (start <= end) return timeValue >= start && timeValue <= end;
    return timeValue >= start || timeValue <= end;
}

function transfersTotal() {
    const configEl = document.getElementById('transfer-pricing-config');
    const rows = document.querySelectorAll('#transfer-rows .transfer-row');
    if (!configEl || !rows.length) return 0;

    const config = JSON.parse(configEl.textContent);
    const bands = [...config.bands].sort((a, b) => a.max_guests - b.max_guests);

    let total = 0;
    rows.forEach((row) => {
        const adults = parseInt(row.querySelector('[name="transfer_adults[]"]')?.value, 10) || 0;
        const children = parseInt(row.querySelector('[name="transfer_children[]"]')?.value, 10) || 0;
        const infants = parseInt(row.querySelector('[name="transfer_infants[]"]')?.value, 10) || 0;
        const totalGuests = adults + children + infants;
        if (totalGuests < 1) return;

        const band = bands.find((b) => b.max_guests >= totalGuests);
        if (!band) return;

        const time = row.querySelector('[name="transfer_time[]"]')?.value;
        const surcharge = isNightTime(config, time) ? parseFloat(config.night_surcharge) : 0;
        total += parseFloat(band.price) + surcharge;
    });
    return total;
}

function requestedExtrasTotal() {
    let total = 0;
    document.querySelectorAll('.request-row-qty').forEach((input) => {
        const qty = parseInt(input.value, 10) || 0;
        const price = parseFloat(input.dataset.price) || 0;
        total += qty * price;
    });
    return total;
}

function computeTotal() {
    if (!totalValue) return;
    const total = flatPrice('welcome_pack_checkbox') + flatPrice('late_checkout_checkbox')
        + flatPrice('mid_stay_clean_checkbox') + cotHighChairPrice() + transfersTotal() + requestedExtrasTotal();
    totalValue.textContent = `€${total.toFixed(2)}`;
}

if (totalValue) {
    document.addEventListener('input', computeTotal);
    document.addEventListener('change', computeTotal);
    document.addEventListener('click', computeTotal);
    computeTotal();
}
