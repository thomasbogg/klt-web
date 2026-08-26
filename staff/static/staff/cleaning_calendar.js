(function () {
    // Fallback palette for a location with no curated Location.color, indexed by location_id
    // (or property_id if the property has no location at all) - just needs to visually separate
    // things on the grid when nobody's picked a real colour yet.
    var LOCATION_COLOR_FALLBACK = [
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
        var detailUrlTemplate = el.dataset.detailUrlTemplate;
        var saveUrlTemplate = el.dataset.saveUrlTemplate;
        var csrftoken = getCookie('csrftoken');

        var dialog = document.getElementById('clean-task-dialog');
        var dialogBody = document.getElementById('clean-task-dialog-body');
        var currentTaskId = null;
        document.getElementById('clean-task-dialog-close').addEventListener('click', function () {
            dialog.close();
        });
        document.getElementById('clean-task-dialog-save').addEventListener('click', function () {
            if (currentTaskId != null) saveTask(currentTaskId);
        });

        // Tracks which day cells are currently shaded invalid mid-drag, so eventDragStop can
        // clear exactly those without re-querying the whole grid.
        var shadedCells = [];

        function shadeInvalidDays(minDate, maxDate) {
            var cells = el.querySelectorAll('.fc-daygrid-day[data-date]');
            cells.forEach(function (cell) {
                var cellDate = cell.getAttribute('data-date');
                var tooEarly = cellDate < minDate;
                var tooLate = maxDate != null && cellDate > maxDate;
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
            locale: 'en-gb',
            headerToolbar: {
                left: 'prev,next today',
                center: 'title',
                right: 'dayGridMonth,cleaningWeek',
            },
            views: {
                // A genuinely custom view, not an override of the built-in dayGridWeek - per-view
                // overrides of an existing view's dateIncrement aren't reliably honoured by
                // FullCalendar's navigation, but a view defined from scratch with its own
                // duration/dateIncrement is. Stepping a full 7 days at a time would mean a Sunday
                // and the following Monday are never visible together, so a clean could never be
                // dragged across that boundary - dateIncrement shifts prev/next by less than the
                // view's own 7-day span, giving a rolling window that eventually shows both sides
                // of every week seam.
                cleaningWeek: {
                    type: 'dayGrid',
                    duration: { days: 7 },
                    dateIncrement: { days: 3 },
                    buttonText: 'week',
                },
            },
            editable: true,
            eventStartEditable: true,
            eventDurationEditable: false,
            dayMaxEventRows: 4,
            events: eventsUrl,

            eventDidMount: function (info) {
                var props = info.event.extendedProps;
                var fallbackKey = props.location_id != null ? props.location_id : props.property_id;
                var color = props.location_color || LOCATION_COLOR_FALLBACK[fallbackKey % LOCATION_COLOR_FALLBACK.length];
                info.el.style.backgroundColor = color;
                info.el.style.borderColor = color;
                if (props.status === 'done') {
                    info.el.style.opacity = '0.55';
                }
            },

            eventDragStart: function (info) {
                shadeInvalidDays(
                    info.event.extendedProps.min_date,
                    info.event.extendedProps.max_date,
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
                var taskId = info.event.id;
                var detailUrl = detailUrlTemplate.replace('0', taskId);
                fetch(detailUrl).then(function (response) {
                    return response.json();
                }).then(function (data) {
                    if (data.error) {
                        showError(data.error);
                        return;
                    }
                    dialogBody.innerHTML = data.departure_html + (data.arrival_html || '') + data.planner_html;
                    currentTaskId = taskId;
                    initDatePicker();
                    dialog.showModal();
                }).catch(function () {
                    showError('Could not load this task.');
                });
            },
        });

        function initDatePicker() {
            if (typeof flatpickr === 'undefined') return;
            var input = dialogBody.querySelector('[name="date"]');
            if (!input) return;
            flatpickr(input, {
                dateFormat: 'Y-m-d', altInput: true, altFormat: 'd/m/Y', allowInput: true,
                // `static` keeps the calendar as a normal-flow sibling of the input (via
                // flatpickr's own positioning wrapper), absolutely positioned relative to that
                // wrapper rather than the viewport - the standard fix for a picker rendering in
                // the wrong place inside a scrollable/modal container. Matches the hidden-until-
                // clicked, overlay-on-top-of-content behaviour every other date field in this app
                // already has (staff/static/staff/date_range_pair.js). The dialog body's own
                // overflow-y/min-height (cleaning_calendar.css) is what actually lets a calendar
                // taller than the visible dialog scroll into view - `static` alone doesn't fix
                // that part.
                static: true,
                minDate: input.dataset.minDate || null,
                maxDate: input.dataset.maxDate || null,
            });
        }

        function saveTask(taskId) {
            var saveUrl = saveUrlTemplate.replace('0', taskId);
            var dateInput = dialogBody.querySelector('[name="date"]');
            var checked = dialogBody.querySelectorAll('[name="assigned_to"]:checked');
            var params = new URLSearchParams();
            params.set('date', dateInput.value);
            checked.forEach(function (box) { params.append('assigned_to', box.value); });

            fetch(saveUrl, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': csrftoken,
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: params.toString(),
            }).then(function (response) {
                var isJson = (response.headers.get('Content-Type') || '').indexOf('application/json') !== -1;
                return (isJson ? response.json() : Promise.resolve({})).then(function (data) {
                    if (!response.ok) {
                        throw new Error(data.error || 'Could not save this task.');
                    }
                    dialog.close();
                    calendar.refetchEvents();
                });
            }).catch(function (err) {
                showError(err.message);
            });
        }

        calendar.render();
    });
})();
