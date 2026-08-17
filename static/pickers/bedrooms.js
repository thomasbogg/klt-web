import { Picker } from "./picker.js";

export class Bedroomspicker extends Picker {
    constructor(name){
        super(name);

        this.placeholder = 'Any bedrooms';
        this.options = this.picker.querySelectorAll('.option');

        for (const option of this.options){
            option.addEventListener('click', () => this.selectOption(option));
        }

        this.display();
    }

    selectOption(option){
        this.value = option.textContent.trim();
        this.close();
        this.display();
    }

    display(){
        for (const option of this.options){
            option.classList.toggle('selected', option.textContent.trim() === this.value);
        }
    }
}
