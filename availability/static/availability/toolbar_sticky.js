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

    // the location/bedrooms pickers are omitted from the toolbar when it's
    // pre-filled from a search, so only wire them up if present
    const bedroomsEl = document.querySelector('.container.picker.visible.bedrooms');
    const bedroomsPicker = bedroomsEl ? new BedroomsGrouppicker(endPicker.dates) : null;
    const locationEl = document.querySelector('.container.picker.visible.location');
    const locationPicker = locationEl ? new Locationpicker('location') : null;

    const form = document.querySelector('form.toolbar.availability');
    const submitBtn = document.querySelector('form.toolbar.availability button.submit');

    startPicker.dates.addEventListener('click', () => switchStartToEndPicker(startPicker, endPicker));
    endPicker.dates.addEventListener('click', () => switchEndToGuestsPicker(endPicker, guestsPicker));

    if (submitBtn && form) submitBtn.addEventListener('click', (e) => submissionValidation(e, startPicker.value, endPicker.value));
});
