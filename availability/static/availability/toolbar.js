import { Datepicker } from '../../../static/pickers/dates.js';
import { Grouppicker } from '../../../static/pickers/groups.js';


const selectedDate = new Date();
const disableBefore = new Date();
const disableAfter = null;


export class StartDatepicker extends Datepicker{
    constructor(){
        super('start', selectedDate, disableBefore, disableAfter);
        this.placeholder = 'Check-in';
    }
}

export class EndDatepicker extends Datepicker{
    constructor(toolbarStartDatepicker){
        super('end',  selectedDate, disableBefore, disableAfter);
        this.placeholder = 'Check-out';
        this.toolbarStartDatepicker = toolbarStartDatepicker;
    }
    openClosePickerContainer(e){
        if (
            this.toolbarStartDatepicker.dates.contains(e.target)){
                return
        };
        if (
            this.inputter.contains(e.target) &&
            !this.toolbarStartDatepicker.value
        ){
            this.toolbarStartDatepicker.open();
            return;
        }
        super.openClosePickerContainer(e);
    }
}

export class GuestsGrouppicker extends Grouppicker{
    constructor(endDatesPickerElement){
        super('guests');
        this.endDatesPickerElement = endDatesPickerElement;
    }
    openClosePickerContainer(e){
        if (this.endDatesPickerElement.contains(e.target)) return;
        super.openClosePickerContainer(e);
    }
}

export const switchStartToEndPicker = (startPicker, endPicker) => {
    const checkInDate = startPicker.selectedDate;
    const checkInDatePlusOne = addDays(checkInDate, 1);
    
    endPicker.selectedDate = checkInDatePlusOne;
    endPicker.disableBefore = checkInDatePlusOne;

    startPicker.close();
    endPicker.open();
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