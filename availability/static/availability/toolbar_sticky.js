import { createSearchDatePickers, GuestsGrouppicker, submissionValidation, switchStartToEndPicker, switchEndToGuestsPicker } from './toolbar.js';
import { Locationpicker } from '../../../static/pickers/locations.js';
import { Bedroomspicker } from '../../../static/pickers/bedrooms.js';

document.addEventListener('DOMContentLoaded', function() {
    const { startInput, endInput, startFp, endFp } = createSearchDatePickers();
    const guestsPicker = new GuestsGrouppicker(endFp.calendarContainer);

    // the location/bedrooms pickers are omitted from the toolbar when it's
    // pre-filled from a search, so only wire them up if present
    const bedroomsEl = document.querySelector('.container.picker.visible.bedrooms');
    const bedroomsPicker = bedroomsEl ? new Bedroomspicker('bedrooms') : null;
    const locationEl = document.querySelector('.container.picker.visible.location');
    const locationPicker = locationEl ? new Locationpicker('location') : null;

    const form = document.querySelector('form.toolbar.availability');
    const submitBtn = document.querySelector('form.toolbar.availability button.submit');

    startFp.config.onChange.push(() => switchStartToEndPicker(startFp, endFp));
    endFp.config.onChange.push(() => switchEndToGuestsPicker(endFp, guestsPicker));

    if (submitBtn && form) submitBtn.addEventListener('click', (e) => submissionValidation(e, startInput.value, endInput.value));
});
