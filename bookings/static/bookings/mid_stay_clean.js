const checkbox = document.getElementById('mid_stay_clean_checkbox');
const picker = document.getElementById('mid-stay-clean-picker');

function updatePickerVisibility() {
    if (picker && checkbox) {
        picker.classList.toggle('welcome-pack-picker-hidden', !checkbox.checked);
    }
}

if (checkbox) {
    checkbox.addEventListener('change', updatePickerVisibility);
    updatePickerVisibility();
}
