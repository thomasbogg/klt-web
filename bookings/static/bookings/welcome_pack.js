const checkbox = document.getElementById('welcome_pack_checkbox');
const picker = document.getElementById('welcome-pack-picker');
const items = document.querySelectorAll('#welcome-pack-items li');
const foodRadios = document.querySelectorAll('input[name="welcome_pack_food"]');
const drinksRadios = document.querySelectorAll('input[name="welcome_pack_drinks"]');

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
    if (picker && checkbox) {
        picker.classList.toggle('welcome-pack-picker-hidden', !checkbox.checked);
    }
}

if (checkbox) {
    checkbox.addEventListener('change', updatePickerVisibility);
    updatePickerVisibility();
}

[...foodRadios, ...drinksRadios].forEach((radio) => radio.addEventListener('change', updateItems));
updateItems();
