export class Picker {
    static instances = new Set();
    static documentListenerAttached = false;

    constructor(name){
        this.name = name;
        this.container = document.querySelector(`.container.picker.visible.${name}`);
        if (!this.container) {
            throw new Error(`Picker container not found for name: ${name}`);
        }

        this.inputter = this.container.querySelector('input.readonly');
        this.picker = this.container.querySelector('.container.picker.hidden');

        if (!this.inputter || !this.picker) {
            throw new Error(`Picker inputs not found for name: ${name}`);
        }

        this.hide();

        Picker.instances.add(this);
        if (!Picker.documentListenerAttached) {
            document.addEventListener('click', Picker.handleDocumentClick);
            Picker.documentListenerAttached = true;
        }
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
        if (this.isHidden()) {
            this.picker.hidden = false;
            this.display();
        }
    }

    close(){
        if (!this.isHidden()) {
            this.hide();
        }
    }

    hide(){
        this.picker.hidden = true;
    }

    isHidden(){
        return this.picker.hidden === true;
    }

    display(){
        // Child classes implement calendar or group rendering.
    }

    getValueString(){
        // Child classes implement their own display text.
    }

    // Private functions
    openClosePickerContainer(e){
        const target = e.target;

        if (!this.container.contains(target)) {
            this.hide();
            return;
        }

        if (this.picker.contains(target)) {
            return;
        }

        if (this.isHidden()) {
            this.open();
        } else {
            this.close();
        }
    }

    static handleDocumentClick(e){
        for (const instance of Picker.instances) {
            instance.openClosePickerContainer(e);
        }
    }
}