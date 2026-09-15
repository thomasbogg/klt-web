// Scoped per enclosing <form> - see welcome_pack.js for why (one section per apartment on the
// merged multi-property Extras page).

document.querySelectorAll('[data-mid-stay-clean-checkbox]').forEach((checkbox) => {
    const scope = checkbox.closest('form') || document;
    const picker = scope.querySelector('[data-mid-stay-clean-picker]');

    function updatePickerVisibility() {
        if (picker) {
            picker.classList.toggle('welcome-pack-picker-hidden', !checkbox.checked);
        }
    }

    checkbox.addEventListener('change', updatePickerVisibility);
    updatePickerVisibility();
});
