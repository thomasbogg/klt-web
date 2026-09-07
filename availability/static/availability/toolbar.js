import { dateCheck, isDateString } from './script.js';
import { Grouppicker } from '../../../static/pickers/groups.js';
import { linkDateRange, tagCalendar } from '../../../static/pickers/linked_dates.js';

export class GuestsGrouppicker extends Grouppicker {
    constructor(endDatesPickerElement){
        super('guests');
        this.endDatesPickerElement = endDatesPickerElement;
    }
    openClosePickerContainer(e){
        if (this.endDatesPickerElement.contains(e.target)) return;
        super.openClosePickerContainer(e);
    }
}

export function createSearchDatePickers() {
    const startInput = document.getElementById('start');
    const endInput = document.getElementById('end');
    startInput.placeholder = 'Check-in';
    endInput.placeholder = 'Check-out';

    // Not appendTo-ed into the field's own wrapper: this codebase's global
    // .container base rule (static/main/style.css) puts every .container
    // element - including that wrapper - on position:relative, which breaks
    // flatpickr's own viewport-relative positioning math. Defaulting to
    // flatpickr's normal document.body append (same as the staff-side
    // flatpickr usage) keeps positioning correct; tagCalendar below scopes
    // the brand-color theme in dates.css by class instead of DOM nesting.
    const startFp = window.flatpickr(startInput, {
        dateFormat: 'd/m/Y',
        minDate: 'today',
        onReady: [tagCalendar],
    });

    const endFp = window.flatpickr(endInput, {
        dateFormat: 'd/m/Y',
        minDate: 'today',
        onReady: [tagCalendar],
        onOpen: [(selectedDates, dateStr, instance) => {
            // Check-out clicked before check-in has a value - open check-in instead.
            if (!startFp.selectedDates.length) {
                instance.close();
                startFp.open();
            }
        }],
    });

    linkDateRange(startFp, endFp);

    return { startInput, endInput, startFp, endFp };
}

export const switchStartToEndPicker = (startFp, endFp) => {
    startFp.close();
    endFp.open();
}

export const switchEndToGuestsPicker = (endFp, guestsPicker) => {
    endFp.close();
    guestsPicker.open();
}

export const submissionValidation = (e, start, end) => {
    // Basic validation

    if (
        !start ||
        !end
    ){
        e.preventDefault();
        alert('Please select dates');
        return;
    }

    if (
        !isDateString(start) ||
        !isDateString(end)
    ){
        e.preventDefault();
        alert('Please enter valid dates in the format DD/MM/YYYY');
        return;
    }

    if (
        !dateCheck(start, end)
    ){
        e.preventDefault();
        alert('Check-out date must be after check-in date');
        return;
    }

    // If we get here, validation passed
    console.log('Form validation passed, submitting...');
}
