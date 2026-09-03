// Same data-methods/details-section-hidden contract as arrival_departure.js - shows only the
// fields relevant to the selected mode (adjust vs. clone) and disables the hidden ones so they
// don't get posted (the view would otherwise see e.g. a leftover "year" value from before the
// staffer switched to clone mode).
function wireModeSelect() {
    const select = document.getElementById('staff-price-tool-mode');
    if (!select) return;

    const groups = Array.from(document.querySelectorAll('[data-methods]'));

    function update() {
        groups.forEach((group) => {
            const modes = group.dataset.methods.split(' ');
            const show = modes.includes(select.value);
            group.classList.toggle('details-section-hidden', !show);
            group.querySelectorAll('input, select, textarea').forEach((field) => {
                field.disabled = !show;
            });
        });
    }

    select.addEventListener('change', update);
    update();
}

wireModeSelect();
