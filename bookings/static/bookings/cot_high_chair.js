// Scoped per enclosing <form> (2026-09-15) - see welcome_pack.js for why. The pricing config
// itself stays a single page-level element: it's identical for every apartment of a stay (global
// ExtrasSettings prices, and both legs share the same dates by construction - see
// ReservationGroup), so it's rendered once and looked up page-wide rather than per leg.

const configEl = document.getElementById('cot-high-chair-pricing-config');
const config = configEl ? JSON.parse(configEl.textContent) : null;

document.querySelectorAll('[data-cot-checkbox]').forEach((cotCheckbox) => {
    const scope = cotCheckbox.closest('form') || document;
    const highChairCheckbox = scope.querySelector('[data-high-chair-checkbox]');
    const priceValue = scope.querySelector('[data-cot-high-chair-price-value]');
    if (!highChairCheckbox || !priceValue || !config) return;

    function computePrice() {
        const isLongStay = config.nights > 7;
        let total = 0;
        if (cotCheckbox.checked) {
            total += parseFloat(isLongStay ? config.cot_long : config.cot_short);
        }
        if (highChairCheckbox.checked) {
            total += parseFloat(isLongStay ? config.high_chair_long : config.high_chair_short);
        }
        if (cotCheckbox.checked && highChairCheckbox.checked) {
            total -= total * (parseFloat(config.combo_discount_percent) / 100);
        }
        total = Math.max(total, 0);

        priceValue.textContent = (cotCheckbox.checked || highChairCheckbox.checked) ? `€${total.toFixed(2)}` : '–';
    }

    cotCheckbox.addEventListener('change', computePrice);
    highChairCheckbox.addEventListener('change', computePrice);
    computePrice();
});

// The section only shows once the guest has actually typed an infant age into the Guest List
// (not just because they picked "infants" in the original availability search - on a fresh
// booking every age field starts blank regardless of that search selection, see
// BookingDetailsView._any_infant_age) - so this has to react live as ages are typed, not just
// reflect a fixed server-rendered state. Only ever one guest-list form on the pages that pair the
// two (details.html / balance_details.html); the merged Extras page has no guest-list form on it
// at all, and drives this section's visibility server-side instead (see _show_cot_high_chair).
const section = document.querySelector('[data-cot-high-chair-section]');
const guestRows = document.querySelector('[data-guest-rows]');

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
