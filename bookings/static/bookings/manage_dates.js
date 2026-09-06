import { Datepicker } from '../pickers/dates.js';
import { addDays, dateCheck, isDateString } from '../availability/script.js';

class EditDatesStartDatepicker extends Datepicker {
    constructor(disableBefore) {
        super('arrival', new Date(), disableBefore, null);
        this.placeholder = 'Check-in';
    }
}

class EditDatesEndDatepicker extends Datepicker {
    constructor(disableBefore) {
        super('departure', new Date(), disableBefore, null);
        this.placeholder = 'Check-out';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const form = document.querySelector('form.edit-dates-form');
    if (!form) return;

    const dataEl = document.getElementById('edit-dates-data');
    const occupiedRanges = dataEl
        ? JSON.parse(dataEl.textContent).map(([start, end]) => ({
            start: Datepicker.parseValue(start), end: Datepicker.parseValue(end),
        }))
        : [];

    const now = new Date();
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());

    const startPicker = new EditDatesStartDatepicker(today);
    const endPicker = new EditDatesEndDatepicker(addDays(startPicker.selectedDate, 1));
    startPicker.disabledRanges = occupiedRanges;
    endPicker.disabledRanges = occupiedRanges;

    startPicker.dates.addEventListener('dateselected', () => {
        const checkInPlusOne = addDays(startPicker.selectedDate, 1);
        endPicker.disableBefore = checkInPlusOne;
        if (!endPicker.value || !dateCheck(startPicker.value, endPicker.value)) {
            endPicker.selectedDate = checkInPlusOne;
            endPicker.value = endPicker.getValueString();
        }
        startPicker.close();
        endPicker.open();
    });

    endPicker.dates.addEventListener('dateselected', () => endPicker.close());

    form.addEventListener('submit', (e) => {
        const start = startPicker.value;
        const end = endPicker.value;

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
