import { createSearchDatePickers, GuestsGrouppicker, submissionValidation, switchStartToEndPicker, switchEndToGuestsPicker } from './toolbar.js';

document.addEventListener('DOMContentLoaded', function() {
    const { startInput, endInput, startFp, endFp } = createSearchDatePickers();
    const guestsPicker = new GuestsGrouppicker(endFp.calendarContainer);
    const form = document.querySelector('form.toolbar.availability');
    const submitBtn = document.querySelector('form.toolbar.availability button.submit');

    startFp.config.onChange.push(() => switchStartToEndPicker(startFp, endFp));
    endFp.config.onChange.push(() => switchEndToGuestsPicker(endFp, guestsPicker));

    if (submitBtn && form) submitBtn.addEventListener('click', (e) => submissionValidation(e, startInput.value, endInput.value));
});
