import { Picker } from "./picker.js";

export class Grouppicker extends Picker {
    constructor(name = null){
        if (name) name = name + '-group';
        else name = 'group';
        super(name)
        
        this.incrementBtns = this.picker.querySelectorAll('.increment');
        this.decrementBtns = this.picker.querySelectorAll('.decrement');
        this.countDisplays = this.picker.querySelectorAll('.count-display');
           
        this.groupCounts = this.getGroupCounts();
        
        // set increment and decrement buttons
        for (let i = 0; i < this.incrementBtns.length; i++){
       
            this.incrementBtns[i].addEventListener('click', (e) => {
                this.groupCounts[i][1] += 1;
                const id = e.target.getAttribute('id');
                this.display(id);
            });
            
            this.decrementBtns[i].addEventListener('click', (e) => {
                this.groupCounts[i][1] -= 1;
                const id = e.target.getAttribute('id');
                this.display(id);
            });
        }

        this.placeholder = this.getValueString();
        this.inputter.style.width = Math.floor(this.placeholder.length / 2) + 'rem';
        this.display();
    }

    // Initialise counter for group modifications
    getGroupCounts(){
        let counts = [];
        for (let i = 0; i < this.countDisplays.length; i++){
            const title = this.countDisplays[i].getAttribute('id');
            const count = parseInt(this.countDisplays[i].innerHTML);
            const min = parseInt(this.countDisplays[i].getAttribute('min'))
            const max = parseInt(this.countDisplays[i].getAttribute('max'))
            counts.push([title, count, min, max]);
        }
        return counts;
    }

    display(id = null){

        for (let i = 0; i < this.groupCounts.length; i++){
            if(id && id !== this.groupCounts[i][0]) continue;

            const count = this.groupCounts[i][1]
            
            if (count === this.groupCounts[i][2]){
                this.decrementBtns[i].disabled = true;
            } else {
                this.decrementBtns[i].disabled = false;
            }
            
            if (count === this.groupCounts[i][3]){
                this.incrementBtns[i].disabled = true;
            } else {
                this.incrementBtns[i].disabled = false;
            }

            this.countDisplays[i].innerHTML = count;
            this.value = this.getValueString()
        }
    }

    getValueString(){
        let string = ''
        for(let i = 0; i < this.groupCounts.length; i++){
            const group = this.groupCounts[i]
            string += group[1] + ' ' + group[0]
       
            if (i !== this.groupCounts.length - 1) {
                string += ', '
            }
        }
        return string
    }
}

 class GroupItem{
    constructor(title = null, subtitle = null, min = null, max = null, _default = null){
        this._title = title;
        this._subtitle = subtitle;
        this._min = min;
        this._max = max;
        this._default = _default;
    }    

    get title(){
        return this._title;
    }

    set title(value){
        this._title = value;
    }

    get subtitle(){
        return this._subtitle;
    }

    set subtitle(value){
        this._subtitle = value;
    }

    get min(){
        return this._min;
    }

    set min(value){
        this._min = value;
    }

    get max(){
        return this._max;
    }

    set max(value){
        this._max = value;
    }

    get default(){
        return this._default;
    }

    set default(value){
        this._default = value;
    }

    insert(){
        // code to insert group item into DOM
    }
}