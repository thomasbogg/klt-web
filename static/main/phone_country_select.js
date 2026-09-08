document.addEventListener('DOMContentLoaded', function () {
    // Country code select shows just the calling code ("+351") once collapsed, so it can stay
    // narrow without clipping - but the full option list still needs each country's name to be
    // usable, so the currently-selected option's own displayed text is swapped between the two
    // forms rather than actually changing what's stored (option.value, and so the posted
    // phone_country_code, never changes). Shared 2026-09-08 by every page with a
    // phone_country_code dropdown (Owner Suite Contact Details, the guest reservation form,
    // Manage Booking Contact Details) - querySelectorAll rather than querySelector since more
    // than one such select can exist on a page.
    var selects = document.querySelectorAll('select[name="phone_country_code"]');

    selects.forEach(function (select) {
        Array.prototype.forEach.call(select.options, function (option) {
            option.dataset.fullLabel = option.textContent;
        });

        function showCodeOnly() {
            var selected = select.options[select.selectedIndex];
            if (selected) {
                selected.textContent = selected.value || selected.dataset.fullLabel;
            }
        }

        function showFullNames() {
            Array.prototype.forEach.call(select.options, function (option) {
                option.textContent = option.dataset.fullLabel;
            });
        }

        showCodeOnly();
        // mousedown/focus fire before the dropdown list actually opens (click alone would be too
        // late), so every option reads with its full country name by the time it's visible.
        select.addEventListener('mousedown', showFullNames);
        select.addEventListener('focus', showFullNames);
        select.addEventListener('change', showCodeOnly);
        select.addEventListener('blur', showCodeOnly);
    });
});
