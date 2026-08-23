document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-copy-target]');
    if (!button) return;
    var input = document.getElementById(button.getAttribute('data-copy-target'));
    if (!input) return;

    var showCopied = function () {
        var original = button.textContent;
        button.textContent = 'Copied!';
        setTimeout(function () { button.textContent = original; }, 1500);
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(input.value).then(showCopied);
    } else {
        input.select();
        document.execCommand('copy');
        showCopied();
    }
});

document.addEventListener('DOMContentLoaded', function () {
    if (typeof flatpickr === 'undefined') return;

    var containers = new Set();
    document.querySelectorAll('.staff-date-start, .staff-date-end').forEach(function (input) {
        var container = input.closest('tr') || input.closest('.staff-price-form-row');
        if (container) containers.add(container);
    });

    containers.forEach(function (container) {
        var startInput = container.querySelector('.staff-date-start');
        var endInput = container.querySelector('.staff-date-end');
        if (!startInput || !endInput) return;

        var baseOptions = { dateFormat: 'Y-m-d', altInput: true, altFormat: 'd/m/Y', allowInput: true };
        var startPicker, endPicker;

        startPicker = flatpickr(startInput, Object.assign({}, baseOptions, {
            maxDate: endInput.value || null,
            onChange: function (dates) {
                endPicker.set('minDate', dates[0] || null);
            }
        }));
        endPicker = flatpickr(endInput, Object.assign({}, baseOptions, {
            minDate: startInput.value || null,
            onChange: function (dates) {
                startPicker.set('maxDate', dates[0] || null);
            }
        }));
    });
});
