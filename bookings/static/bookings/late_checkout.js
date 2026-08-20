const checkbox = document.getElementById('late_checkout_checkbox');
const picker = document.getElementById('late-checkout-picker');

function updatePickerVisibility() {
    if (picker && checkbox) {
        picker.classList.toggle('welcome-pack-picker-hidden', !checkbox.checked);
    }
}

if (checkbox) {
    checkbox.addEventListener('change', updatePickerVisibility);
    updatePickerVisibility();
}
