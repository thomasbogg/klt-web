import { StartDatepicker, EndDatepicker, GuestsGrouppicker, submissionValidation, switchStartToEndPicker, switchEndToGuestsPicker } from './toolbar.js';

document.addEventListener('DOMContentLoaded', function() {
    const startPicker = new StartDatepicker();
    const endPicker = new EndDatepicker(startPicker);
    const guestsPicker = new GuestsGrouppicker(endPicker.dates);
    const form = document.querySelector('form.toolbar.availability');
    const submitBtn = document.querySelector('form.toolbar.availability button.submit');

    startPicker.dates.addEventListener('click', switchStartToEndPicker(startPicker, endPicker));
    endPicker.dates.addEventListener('click', switchEndToGuestsPicker(endPicker, guestsPicker));
    
    if (submitBtn && form) submitBtn.addEventListener('click', submissionValidation(e, startPicker.value, endPicker.value));
});