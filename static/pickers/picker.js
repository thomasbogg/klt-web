export class Picker {
    constructor(name){
        this.name = name;
        // select container with input element and the picker container
        this.container = document.querySelector(`.${name}picker-container`);
        // select the clickable input element that raises/hides the picker element 
        this.inputter = this.container.querySelector('input[class$="picker-input"]'); 
        // select the picker element that is raised and hidden accordingly
        this.picker = this.container.querySelector('div[class$="picker"');

        document.addEventListener('click', (e) => {
            this.openClosePickerContainer(e);
        });

        this.#printDebugInfo(name);
    }

    // Getters and Setters
    get placeholder(){
        return this.inputter.placeholder;
    }

    set placeholder(value){
        this.inputter.placeholder = value;
    }

    get value(){
        return this.inputter.value;
    }

    set value(value){
        this.inputter.value = value;
    }
    
    // Class functions
    open(){
        if (this.picker.hidden) {
            this.picker.hidden = false;
            this.display();
        }
    }

    close(){
        if (!this.picker.hidden) this.picker.hidden = true;
    }

    display(){
        // the picker appearance creator and updater
        // to be set up in the child class
    }

    getValueString(){
        // the string rendition to set the value of the inputter
        // to be set up in the child class
    }

    // Private fuctions
    openClosePickerContainer(e){
        // if not clicking any part of the container, hide
        if (!this.container.contains(e.target)) {
            this.picker.hidden = true;
            return
        }
        // if clicking inside the picker, ignore
        if (this.picker.contains(e.target)) return;
        // if clicking the input, open if hidden and vice-versa
        if (this.picker.hidden) this.picker.hidden = false;
        else this.picker.hidden = true;
    }

    #printDebugInfo(name){
        console.log('----------------')
        console.log('Debug info for Picker initialised with name:', name);
        console.log('- main container is:', this.container);
        console.log('- inputter is:', this.inputter);
        console.log('- picker is:', this.picker);
    }
}