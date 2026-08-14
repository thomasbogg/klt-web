import { StartDatepicker, EndDatepicker, GuestsGrouppicker, submissionValidation, switchStartToEndPicker, switchEndToGuestsPicker } from './toolbar.js';
import { Grouppicker } from '../../../static/pickers/groups.js';
import { Locationpicker } from '../../../static/pickers/locations.js';

class BedroomsGrouppicker extends Grouppicker{
    constructor(endDatesPickerElement){
        super('bedrooms');
        this.endDatesPickerElement = endDatesPickerElement;
    }
    openClosePickerContainer(e){
        if (this.endDatesPickerElement.contains(e.target)) return;
        super.openClosePickerContainer(e);
    }
}

document.addEventListener('DOMContentLoaded', function() {
    const startPicker = new StartDatepicker();
    const endPicker = new EndDatepicker(startPicker);
    const guestsPicker = new GuestsGrouppicker(endPicker.dates);
    const bedroomsPicker = new BedroomsGrouppicker(endPicker.dates);
    const locationPicker = new Locationpicker('location');
    const form = document.querySelector('form.toolbar.availability');
    const submitBtn = document.querySelector('form.toolbar.availability button.submit');

    startPicker.dates.addEventListener('click', () => switchStartToEndPicker(startPicker, endPicker));
    endPicker.dates.addEventListener('click', () => switchEndToGuestsPicker(endPicker, guestsPicker));

    if (submitBtn && form) submitBtn.addEventListener('click', (e) => submissionValidation(e, startPicker.value, endPicker.value));
});
