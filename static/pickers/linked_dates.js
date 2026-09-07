// Keeps an end-date flatpickr's minDate synced to a start-date flatpickr's
// selection, matching Booking.arrival_date/departure_date semantics (a stay
// needs at least one night). Bumps the end date forward if the existing
// selection is no longer valid. Each call site still owns its own onChange
// chaining (what happens next) since that differs per use.
export function linkDateRange(startFp, endFp, { minGapDays = 1 } = {}) {
    const addDays = (date, days) => {
        const result = new Date(date);
        result.setDate(result.getDate() + days);
        return result;
    };

    startFp.config.onChange.push((selectedDates) => {
        const start = selectedDates[0];
        if (!start) return;

        const minEnd = addDays(start, minGapDays);
        endFp.set('minDate', minEnd);

        // triggerChange=false: this only needs to update end's own displayed
        // value, not re-fire end's onChange chain (which would otherwise
        // advance to whatever comes after end, e.g. the guests picker, as an
        // unwanted side effect of picking a start date).
        const end = endFp.selectedDates[0];
        if (!end || end < minEnd) {
            endFp.setDate(minEnd, false);
        }
    });
}

// Marks a flatpickr instance's popup with a class so dates.css can scope the
// brand-color theme to guest-facing calendars without needing DOM nesting
// (flatpickr appends its popup to document.body by default - see the
// appendTo comment in toolbar.js/manage_dates.js for why that's deliberate).
export function tagCalendar(selectedDates, dateStr, instance) {
    instance.calendarContainer.classList.add('guest-datepicker');
}

// Converts occupied ranges - [{start: Date, end: Date}, ...], end exclusive
// (matches Booking.arrival_date/departure_date semantics: the departure day
// itself is free) - into flatpickr's native `disable` option, which is
// inclusive on both ends.
export function toFlatpickrDisabledRanges(ranges) {
    return ranges.map(({ start, end }) => {
        const inclusiveEnd = new Date(end);
        inclusiveEnd.setDate(inclusiveEnd.getDate() - 1);
        return { from: start, to: inclusiveEnd };
    });
}
