import { linkDateRange, tagCalendar, toFlatpickrDisabledRanges } from '../../../static/pickers/linked_dates.js';
import { addDays, dateCheck, isDateString, stringToDate } from '../availability/script.js';

document.addEventListener('DOMContentLoaded', () => {
    const form = document.querySelector('form.edit-dates-form');
    if (!form) return;

    const startInput = document.getElementById('arrival');
    const endInput = document.getElementById('departure');

    const dataEl = document.getElementById('edit-dates-data');
    const occupiedRanges = dataEl
        ? JSON.parse(dataEl.textContent).map(([start, end]) => ({
            start: stringToDate(start), end: stringToDate(end),
        }))
        : [];
    const disabledRanges = toFlatpickrDisabledRanges(occupiedRanges);

    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());

    // Not appendTo-ed into the field's own wrapper: this codebase's global
    // .container base rule (static/main/style.css) puts every .container
    // element - including that wrapper - on position:relative, which breaks
    // flatpickr's own viewport-relative positioning math. Defaulting to
    // flatpickr's normal document.body append keeps positioning correct;
    // tagCalendar scopes the brand-color theme in dates.css by class instead
    // of DOM nesting.
    const startFp = window.flatpickr(startInput, {
        dateFormat: 'd/m/Y',
        minDate: today,
        disable: disabledRanges,
        onReady: [tagCalendar],
    });

    const initialCheckIn = startFp.selectedDates[0] || today;
    const endFp = window.flatpickr(endInput, {
        dateFormat: 'd/m/Y',
        minDate: addDays(initialCheckIn, 1),
        disable: disabledRanges,
        onReady: [tagCalendar],
    });

    linkDateRange(startFp, endFp);

    startFp.config.onChange.push(() => {
        startFp.close();
        endFp.open();
    });

    endFp.config.onChange.push(() => endFp.close());

    // Same price-change-overlay pattern as guest_list.js: "Edit dates" just dismisses the
    // overlay so the guest can see/change the picker fields underneath it again - a plain <button
    // type="button">, not a submit, so it never posts anything itself.
    const priceChangeOverlay = document.getElementById('price-change-overlay');
    const priceChangeEditButton = document.getElementById('price-change-edit');
    if (priceChangeEditButton && priceChangeOverlay) {
        priceChangeEditButton.addEventListener('click', () => priceChangeOverlay.remove());
    }

    // Any date change after a price-change warning is shown invalidates it - same reasoning as
    // guest_list.js's own input listener (the guest must resubmit to see a fresh recalculation).
    if (priceChangeOverlay) {
        startFp.config.onChange.push(() => priceChangeOverlay.classList.add('price-change-stale'));
        endFp.config.onChange.push(() => priceChangeOverlay.classList.add('price-change-stale'));
    }

    form.addEventListener('submit', (e) => {
        const start = startInput.value;
        const end = endInput.value;

        if (!start || !end) {
            e.preventDefault();
            alert('Please select both a check-in and check-out date.');
            return;
        }
        if (!isDateString(start) || !isDateString(end)) {
            e.preventDefault();
            alert('Please enter valid dates in the format DD/MM/YYYY.');
            return;
        }
        if (!dateCheck(start, end)) {
            e.preventDefault();
            alert('Check-out must be after check-in.');
        }
    });
});
