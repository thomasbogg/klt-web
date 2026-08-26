(function () {
    // Small fixed palette, indexed by property_id - just needs to visually separate properties
    // on the grid, not carry any other meaning.
    var PROPERTY_COLORS = [
        '#4C6EF5', '#12B886', '#F76707', '#BE4BDB', '#1098AD', '#E64980', '#F59F00', '#495057',
    ];

    function getCookie(name) {
        var match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
        return match ? decodeURIComponent(match[2]) : null;
    }

    function showError(message) {
        window.alert(message);
    }

    document.addEventListener('DOMContentLoaded', function () {
        var el = document.getElementById('cleaning-calendar');
        if (!el) return;

        var eventsUrl = el.dataset.eventsUrl;
        var moveUrlTemplate = el.dataset.moveUrlTemplate;
        var bookingUrlTemplate = el.dataset.bookingUrlTemplate;
        var csrftoken = getCookie('csrftoken');

        // Tracks which day cells are currently shaded invalid mid-drag, so eventDragStop can
        // clear exactly those without re-querying the whole grid.
        var shadedCells = [];

        function shadeInvalidDays(minDate, maxDateExclusiveOrInclusive, taskType) {
            var cells = el.querySelectorAll('.fc-daygrid-day[data-date]');
            cells.forEach(function (cell) {
                var cellDate = cell.getAttribute('data-date');
                var tooEarly = cellDate < minDate;
                var tooLate = maxDateExclusiveOrInclusive != null && (
                    taskType === 'turnover' ? cellDate >= maxDateExclusiveOrInclusive : cellDate > maxDateExclusiveOrInclusive
                );
                if (tooEarly || tooLate) {
                    cell.classList.add('staff-cal-invalid-day');
                    shadedCells.push(cell);
                }
            });
        }

        function clearShadedDays() {
            shadedCells.forEach(function (cell) { cell.classList.remove('staff-cal-invalid-day'); });
            shadedCells = [];
        }

        var calendar = new FullCalendar.Calendar(el, {
            initialView: 'dayGridMonth',
            headerToolbar: {
                left: 'prev,next today',
                center: 'title',
                right: 'dayGridMonth,dayGridWeek',
            },
            firstDay: 1,
            editable: true,
            eventStartEditable: true,
            eventDurationEditable: false,
            dayMaxEventRows: 4,
            events: eventsUrl,

            eventDidMount: function (info) {
                var propertyId = info.event.extendedProps.property_id;
                var color = PROPERTY_COLORS[propertyId % PROPERTY_COLORS.length];
                info.el.style.backgroundColor = color;
                info.el.style.borderColor = color;
                if (info.event.extendedProps.status === 'done') {
                    info.el.style.opacity = '0.55';
                }
            },

            eventDragStart: function (info) {
                shadeInvalidDays(
                    info.event.extendedProps.min_date,
                    info.event.extendedProps.max_date,
                    info.event.extendedProps.task_type,
                );
            },
            eventDragStop: function () {
                clearShadedDays();
            },

            eventDrop: function (info) {
                var moveUrl = moveUrlTemplate.replace('0', info.event.id);
                var newDate = info.event.startStr;
                fetch(moveUrl, {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrftoken,
                        'Content-Type': 'application/x-www-form-urlencoded',
                    },
                    body: 'date=' + encodeURIComponent(newDate),
                }).then(function (response) {
                    if (!response.ok) {
                        var isJson = (response.headers.get('Content-Type') || '').indexOf('application/json') !== -1;
                        return (isJson ? response.json() : Promise.resolve({})).then(function (data) {
                            throw new Error(data.error || 'Could not move this task.');
                        });
                    }
                }).catch(function (err) {
                    showError(err.message);
                    info.revert();
                });
            },

            eventClick: function (info) {
                var reference = info.event.extendedProps.booking_reference;
                if (reference) {
                    window.location.href = bookingUrlTemplate.replace('REFERENCE', reference);
                }
            },
        });

        calendar.render();
    });
})();
