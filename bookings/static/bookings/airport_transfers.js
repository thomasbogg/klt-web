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

// Must stay in step with ExtrasSettings.compute_transfer_price() - this is only the live estimate,
// the authoritative price is always recomputed server-side on save, but showing the guest one
// number and charging another is its own kind of wrong.
//
// The two rates are VEHICLE SIZES, not bands: fill with 8-seaters, and whatever is left over takes
// a 4-seater if it fits in one, otherwise another 8-seater. Returns the vehicle count too, because
// the night surcharge applies per vehicle.
function vehiclesForGuestCount(totalGuests) {
    const bands = [...config.bands].sort((a, b) => a.max_guests - b.max_guests);
    const small = bands[0];
    const large = bands[bands.length - 1];
    if (!small || !large || totalGuests < 1) return null;

    const prices = [];
    for (let i = 0; i < Math.floor(totalGuests / large.max_guests); i += 1) {
        prices.push(parseFloat(large.price));
    }
    const remainder = totalGuests % large.max_guests;
    if (remainder) {
        prices.push(parseFloat(remainder <= small.max_guests ? small.price : large.price));
    }
    return prices;
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

    const vehiclePrices = vehiclesForGuestCount(totalGuests);
    if (!vehiclePrices || !vehiclePrices.length) {
        priceDisplay.textContent = 'contact us';
        return;
    }

    const base = vehiclePrices.reduce((sum, price) => sum + price, 0);
    const surcharge = isNightTime(time)
        ? parseFloat(config.night_surcharge) * vehiclePrices.length
        : 0;
    priceDisplay.textContent = `€${(base + surcharge).toFixed(2)}`;

    // A party needing more than one vehicle should be told so before they arrive at the airport
    // expecting a single car.
    const vehicleNote = row.querySelector('.transfer-row-vehicles');
    if (vehicleNote) {
        vehicleNote.textContent = vehiclePrices.length > 1
            ? ` (${vehiclePrices.length} vehicles)`
            : '';
    }
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
