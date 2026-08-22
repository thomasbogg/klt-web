function wireMethodSelect(selectId) {
    const select = document.getElementById(selectId);
    if (!select) return;

    const groups = Array.from(document.querySelectorAll(`[data-methods]`))
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

wireMethodSelect('arrival_method');
wireMethodSelect('departure_method');
