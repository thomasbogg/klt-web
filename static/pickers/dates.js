import { Picker } from './picker.js'

export class Datepicker extends Picker {
    constructor(name='', selectedDate = new Date(), disableBefore = null, disableAfter = null) {
        
        if (name) name = name + '-date';
        else name = 'date';
        super(name);
        
        //this.header = this.picker.querySelector(".datepicker-header");
        this.yearInput = this.picker.querySelector(".year-input");
        this.monthInput = this.picker.querySelector(".month-input");
        this.nextBtn = this.picker.querySelector(".next");
        this.prevBtn = this.picker.querySelector(".prev");
        this.dates = this.picker.querySelector(".dates");

        this._selectedDate = this.#simplifyDate(selectedDate);
        this.year = selectedDate.getFullYear();
        this.month = selectedDate.getMonth();
        
        this.todaysDate = this.#simplifyDate(new Date());
        this._disableBefore = this.#simplifyDate(disableBefore);
        this._disableAfter = this.#simplifyDate(disableAfter);

        // handle next month nav
        this.nextBtn.addEventListener('click', () => {
            if(this.month === 11) this.year++;
            this.month = (this.month + 1) % 12;
            this.display();
        });

        // handle prev month nav
        this.prevBtn.addEventListener('click', () => {
            if(this.month === 0) this.year--;
            this.month = (this.month - 1 + 12) % 12;
            this.display();
        });

        // handle month nav
        this.monthInput.addEventListener('change', () => {
            this.month = parseInt(this.monthInput.value);
            this.display();
        });

        // handle year nav
        this.yearInput.addEventListener('change', () => {
            this.year = parseInt(this.yearInput.value);
            this.display();
        });

        this.display();
    }

    get selectedDate(){
        return this._selectedDate;
    }

    set selectedDate(date){
        this._selectedDate = this.#simplifyDate(date);
        this.year = date.getFullYear();
        this.month = date.getMonth();
    }

    get disableBefore(){
        return this._disableBefore;
    }

    set disableBefore(value){
        this._disableBefore = this.#simplifyDate(value);
    }

    get disableAfter(){
        return this._disableAfter;
    }

    set disableAfter(value){
        this._disableAfter = this.#simplifyDate(value);
    }

    // render the dates in the calendar interface
    display() {
        // update year and month whenever dates are updated
        this.updateYearMonth();
        
        // clear the dates
        this.dates.innerHTML = "";

        // display the last week of previous month if not Sat
        const lastOfPrevMonth = new Date(this.year, this.month, 0);

        if(lastOfPrevMonth.getDay() < 6){
         
            for(let i = 0; i <= lastOfPrevMonth.getDay(); i++) {
                const text = lastOfPrevMonth.getDate() - lastOfPrevMonth.getDay() + i;
                const button = this.createButton(text, true);
                this.dates.appendChild(button);
            }
        }

        // display the current month
        const lastOfMonth = new Date(this.year, this.month + 1, 0);

        for(let i = 1; i <= lastOfMonth.getDate(); i++) {
            
            // check if button is today's date
            const isToday = this.isToday(i);
            const isDisabled = this.shouldBeDisabled(i, isToday)
            
            const button = this.createButton(i, isDisabled, isToday);
            button.addEventListener('click', (e) => this.handleDateClick(e));
            this.dates.appendChild(button);
        }
        
        // display the first week of next month if not Mon
        if (lastOfMonth.getDay() !== 6) {
            const firstOfNextMonth = new Date(this.year, this.month + 1, 1);

            for(let i = firstOfNextMonth.getDay(); i <= 6; i++) {
                const text = firstOfNextMonth.getDate() - firstOfNextMonth.getDay() + i;
                const button = this.createButton(text, true);
                this.dates.appendChild(button);
            }
        }
    }

    updateYearMonth() {
        this.monthInput.selectedIndex = this.month;
        this.yearInput.value = this.year; 
    }
    
    handleDateClick(e) {
        const button = e.target;
        
        // remove 'selected' class from other buttons
        const selected = this.dates.querySelector('.selected');
        selected && selected.classList.remove('selected');
    
        // add the 'selected' class to current button
        button.classList.add('selected');
    
        // set the selected date
        this._selectedDate = new Date(this.year, this.month, parseInt(button.textContent));
        this.inputter.value = this.getValueString();
    
    }
    
    createButton(text, isDisabled = false, isToday = false) {
        const button = document.createElement('button');
        button.textContent = text;
        button.disabled = isDisabled;
        button.type = "button";
        button.classList.toggle('today', isToday);
        button.classList.toggle('selected', this.isSelectedDate(text));
        return button;
    }
    
    isToday(i){
        return (
            this.todaysDate.getDate() === i &&
            this.todaysDate.getMonth() === this.month &&
            this.todaysDate.getFullYear() === this.year
        );
        
    }
    
    isSelectedDate(i){
        return(
            this._selectedDate.getDate() === i &&
            this._selectedDate.getMonth() === this.month &&
            this._selectedDate.getFullYear() === this.year
        );
    }
    
    shouldBeDisabled(i){
        const date = new Date(this.year, this.month, i);
    
        // if disableBefore is set to a date not check if is prior date
        if (this._disableBefore){
            return date.valueOf() < this._disableBefore.valueOf()
        }

        // if disableAfter is set to a date not check if is posterior date
        if (this._disableAfter){
            return date.valueOf() > this._disableAfter.valueOf()
        }
        
        return (
            date.valueOf() < this.todaysDate.valueOf()
        );
    }

    getValueString(){
        return (
            this._selectedDate.toLocaleDateString(
                'en-UK', {
                    year: 'numeric',
                    month: '2-digit',
                    day: '2-digit'
                }
            )
        );
    }

    #simplifyDate(date = null){
        if(!date) return null;
        return new Date(date.getFullYear(), date.getMonth(), date.getDate());
    }
}

// get dates in JS
// new Date().toDateString() -> 'Wed Dec 03 2025'
// new Date().getFullYear() -> current year
// new Date().getYear() -> current year - 1900
// new Date().getMonth() -> current month from 0 index (January)
// new Date().getDate() -> current day of the month
// new Date().getDay() -> current day of the week from 0 index (Sunday)
// new Date(2024, 1, 1).toDateString() -> string repr of given date w/ weekday  