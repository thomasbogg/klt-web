import { dateCheck, isDateString, addDays } from './script.js'
import { Datepicker } from '../../../static/pickers/dates.js';
import { Grouppicker } from '../../../static/pickers/groups.js';

const selectedDate = new Date();
const disableBefore = new Date();
const disableAfter = null;

class ToolbarStartDatepicker extends Datepicker{
    constructor(){
        super('start', selectedDate, disableBefore, disableAfter);
    }
}

class ToolbarEndDatepicker extends Datepicker{
    constructor(toolbarStartDatepicker){
        super('end',  selectedDate, disableBefore, disableAfter);
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

class ToolbarGrouppicker extends Grouppicker{
    constructor(endDatesPickerElement){
        super('guests');
        this.endDatesPickerElement = endDatesPickerElement;
    }
    openClosePickerContainer(e){
        if (this.endDatesPickerElement.contains(e.target)) return;
        super.openClosePickerContainer(e);
    }
}

document.addEventListener('DOMContentLoaded', function() {
    const toolbarStartDatepicker = new ToolbarStartDatepicker();
    const toolbarEndDatepicker = new ToolbarEndDatepicker(toolbarStartDatepicker);
    const toolbarGrouppicker = new ToolbarGrouppicker(toolbarEndDatepicker.dates);
    
    const form = document.querySelector('form.toolbar.availability');
    const submitBtn = document.querySelector('form.toolbar.availability button.submit');

    toolbarStartDatepicker.placeholder = 'Check-in';
    toolbarEndDatepicker.placeholder = 'Check-out';

    toolbarStartDatepicker.dates.addEventListener('click', () => {
        const checkInDate = toolbarStartDatepicker.selectedDate;
        const checkInDatePlusOne = addDays(checkInDate, 1);
        
        toolbarEndDatepicker.selectedDate = checkInDatePlusOne;
        toolbarEndDatepicker.disableBefore = checkInDatePlusOne;
        
        toolbarStartDatepicker.close();
        toolbarEndDatepicker.open();
    });

    toolbarEndDatepicker.dates.addEventListener('click', () => {
        toolbarEndDatepicker.close();
        toolbarGrouppicker.open();
    });
    
    // Add form validation if needed
    if (submitBtn && form) {
        submitBtn.addEventListener('click', function(e) {
            // Basic validation
            
            if (
                !toolbarStartDatepicker.value ||
                !toolbarEndDatepicker.value
            ){
                e.preventDefault();
                alert('Please select dates');
                return;
            }

            if (
                !isDateString(toolbarStartDatepicker.value) || 
                !isDateString(toolbarEndDatepicker.value)
            ){
                e.preventDefault();
                alert('Please enter valid dates in the format DD/MM/YYYY');
                return;
            }

            if (
                !dateCheck(toolbarStartDatepicker.value, toolbarEndDatepicker.value)
            ){
                e.preventDefault();
                alert('Check-out date must be after check-in date');
                return;
            }
            
            // If we get here, validation passed
            console.log('Form validation passed, submitting...');
        });
    }
    
});