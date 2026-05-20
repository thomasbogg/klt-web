export const dateCheck = (start, end) => {
    if (typeof(start) === 'string') {
        start = start.split('/')
    }    
    if (typeof(end) === 'string') {
        end = end.split('/')
    }    
    if (
        new Date(parseInt(start[2]), parseInt(start[1]) - 1, parseInt(start[0])) >=
        new Date(parseInt(end[2]), parseInt(end[1]) - 1, parseInt(end[0]))
    ) {
        return false
    }    
    return true
}    

export const isDateString = (string) => {
    if (typeof(string) != 'string') {
        return false
    }
    if (string.split('/').length != 3) {
        return false
    }
    if(!RegExp('^\\d{2}/\\d{2}/\\d{4}$').test(string)) {
        return false
    }
    return true
}

export const stringToDate = (string) => {
    if (isDateString(string)) {
        const stringArray = string.split('/');
        const day = parseInt(stringArray[0]);
        const month = parseInt(stringArray[1]) - 1;
        const year = parseInt(stringArray[2]);
        const date = new Date(year, month, day);
        return date
    }   
}

export const addDays = (date, days = 1) => {
    date = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    date.setDate(date.getDate() + days);
    return date;
}