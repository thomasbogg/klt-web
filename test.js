class Foo {
    constructor(value){
        this._value = value;
    }
    get value(){
        return this._value;
    }
    set value(value){
        this._value = value;
    }
    run(){
        console.log('running');
        return this;
    }
}

console.log(new Foo('bar').value)

class Bar extends Foo {
    constructor(subvalue){
        super('previous value')
        this._subvalue = subvalue;
    }
    
    run(){
        super.run();
        console.log('and running');
        return this;
    }
    
}

new Bar('baz').run().run()