// Ported from bookings/static/bookings/arrival_departure.js (the guest-facing Manage Booking
// hub) - same data-methods/details-section-hidden contract, now reused for the staff booking
// detail page's Arrival/Departure panels and the Owner-booking-conditional row in Booking Info.
function wireMethodSelect(selectId) {
    const select = document.getElementById(selectId);
    if (!select) return;

    const groups = Array.from(document.querySelectorAll('[data-methods]'))
        .filter((group) => select.closest('section').contains(group));

    function update() {
        groups.forEach((group) => {
            const methods = group.dataset.methods.split(' ');
            const show = methods.includes(select.value);
            group.classList.toggle('details-section-hidden', !show);
            group.querySelectorAll('input, select, textarea').forEach((field) => {
                field.disabled = !show;
            });
        });
    }

    select.addEventListener('change', update);
    update();
}

function wireOwnerCheckbox(checkboxId, groupSelector) {
    const checkbox = document.getElementById(checkboxId);
    const group = document.querySelector(groupSelector);
    if (!checkbox || !group) return;

    function update() {
        const show = checkbox.checked;
        group.classList.toggle('details-section-hidden', !show);
        group.querySelectorAll('input, select, textarea').forEach((field) => {
            field.disabled = !show;
        });
    }

    checkbox.addEventListener('change', update);
    update();
}

wireMethodSelect('staff-arrival-method');
wireMethodSelect('staff-departure-method');
wireOwnerCheckbox('staff-is-owner', '[data-owner-row]');
wireOwnerCheckbox('staff-mid-stay-clean', '[data-mid-stay-clean-row]');
