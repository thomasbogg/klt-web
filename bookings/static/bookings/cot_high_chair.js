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

// The section only shows once the guest has actually typed an infant age into the Guest List
// (not just because they picked "infants" in the original availability search - on a fresh
// booking every age field starts blank regardless of that search selection, see
// BookingDetailsView._any_infant_age) - so this has to react live as ages are typed, not just
// reflect a fixed server-rendered state.
const section = document.getElementById('cot-high-chair-section');
const guestRows = document.getElementById('guest-rows');

function hasInfantAge() {
    if (!config || !guestRows) return false;
    const ageInputs = guestRows.querySelectorAll('input[name="age[]"]');
    return Array.from(ageInputs).some((input) => {
        const value = parseInt(input.value, 10);
        return !isNaN(value) && value < config.child_min_age;
    });
}

function updateSectionVisibility() {
    if (!section) return;
    section.classList.toggle('details-section-hidden', !hasInfantAge());
}

if (guestRows) {
    guestRows.addEventListener('input', updateSectionVisibility);
    updateSectionVisibility();
}
