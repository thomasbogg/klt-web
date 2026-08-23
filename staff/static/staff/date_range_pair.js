document.addEventListener('DOMContentLoaded', function () {
    if (typeof flatpickr === 'undefined') return;

    var containers = new Set();
    document.querySelectorAll('.staff-date-start, .staff-date-end').forEach(function (input) {
        var container = input.closest('tr') || input.closest('.staff-field-row');
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

    document.querySelectorAll('.staff-date-input:not(.staff-date-start):not(.staff-date-end)').forEach(function (input) {
        flatpickr(input, { dateFormat: 'Y-m-d', altInput: true, altFormat: 'd/m/Y', allowInput: true });
    });
});
