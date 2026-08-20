const configEl = document.getElementById('transfer-pricing-config');
const config = configEl
    ? JSON.parse(configEl.textContent)
    : { bands: [], night_start: '22:00', night_end: '06:00', night_surcharge: '0' };

const container = document.getElementById('transfer-rows');
const template = document.getElementById('transfer-row-template');
const addButton = document.getElementById('transfer-row-add');

function isNightTime(timeValue) {
    // HH:MM strings from <input type="time"> compare correctly as plain strings since they're
    // fixed-width and zero-padded - no need to parse them into real time objects.
    if (!timeValue) return false;
    const { night_start: start, night_end: end } = config;
    if (start <= end) {
        return timeValue >= start && timeValue <= end;
    }
    return timeValue >= start || timeValue <= end;
}

function priceForGuestCount(totalGuests) {
    const band = [...config.bands]
        .sort((a, b) => a.max_guests - b.max_guests)
        .find((b) => b.max_guests >= totalGuests);
    return band ? parseFloat(band.price) : null;
}

function computePrice(row) {
    const adults = parseInt(row.querySelector('[name="transfer_adults[]"]').value, 10) || 0;
    const children = parseInt(row.querySelector('[name="transfer_children[]"]').value, 10) || 0;
    const infants = parseInt(row.querySelector('[name="transfer_infants[]"]').value, 10) || 0;
    const totalGuests = adults + children + infants;
    const time = row.querySelector('[name="transfer_time[]"]').value;
    const priceDisplay = row.querySelector('.transfer-row-price-value');
    if (!priceDisplay) return;

    if (totalGuests < 1) {
        priceDisplay.textContent = '–';
        return;
    }

    const basePrice = priceForGuestCount(totalGuests);
    if (basePrice === null) {
        priceDisplay.textContent = 'contact us';
        return;
    }

    const surcharge = isNightTime(time) ? parseFloat(config.night_surcharge) : 0;
    priceDisplay.textContent = `€${(basePrice + surcharge).toFixed(2)}`;
}

function wireRow(row) {
    ['transfer_adults[]', 'transfer_children[]', 'transfer_infants[]', 'transfer_time[]'].forEach((name) => {
        const input = row.querySelector(`[name="${name}"]`);
        if (input) input.addEventListener('input', () => computePrice(row));
    });
    computePrice(row);
}

if (container) {
    container.querySelectorAll('.transfer-row').forEach(wireRow);

    container.addEventListener('click', (event) => {
        if (event.target.matches('.transfer-row-remove')) {
            event.target.closest('.transfer-row').remove();
        }
    });
}

if (addButton && template && container) {
    addButton.addEventListener('click', () => {
        container.appendChild(template.content.cloneNode(true));
        wireRow(container.lastElementChild);
    });
}
