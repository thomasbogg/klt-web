// Scoped per enclosing <form>, not per page (2026-09-15): the merged multi-property Extras page
// renders one of these sections per apartment, so ids (and page-wide radio lookups) would bind
// every apartment's picker to the first one's checkbox. Each leg is its own <form>, which also
// keeps the same-named radio groups independent between apartments. Single-form pages
// (details.html, balance_details.html, a single-property manage_extras.html) are unaffected.

document.querySelectorAll('[data-welcome-pack-checkbox]').forEach((checkbox) => {
    const scope = checkbox.closest('form') || document;
    const picker = scope.querySelector('[data-welcome-pack-picker]');
    const items = scope.querySelectorAll('[data-welcome-pack-items] li');
    const foodRadios = scope.querySelectorAll('input[name="welcome_pack_food"]');
    const drinksRadios = scope.querySelectorAll('input[name="welcome_pack_drinks"]');

    function currentChoice(radios) {
        const checked = Array.from(radios).find((radio) => radio.checked);
        return checked ? checked.value : null;
    }

    function updateItems() {
        const food = currentChoice(foodRadios);
        const drinks = currentChoice(drinksRadios);
        items.forEach((item) => {
            const category = item.dataset.category;
            const visible = category.startsWith('food_')
                ? (category === `food_${food}` || category === 'food_common')
                : (category === `drinks_${drinks}` || category === 'drinks_common');
            item.classList.toggle('welcome-pack-item-hidden', !visible);
        });
    }

    function updatePickerVisibility() {
        if (picker) {
            picker.classList.toggle('welcome-pack-picker-hidden', !checkbox.checked);
        }
    }

    checkbox.addEventListener('change', updatePickerVisibility);
    updatePickerVisibility();

    [...foodRadios, ...drinksRadios].forEach((radio) => radio.addEventListener('change', updateItems));
    updateItems();
});
