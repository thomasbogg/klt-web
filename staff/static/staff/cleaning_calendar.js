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

        // Visual banding within a day cell: unassigned cleans (no assigned_to yet) always sort
        // first, then assigned ones by team (Group 1/2/3) - lets a manager see at a glance how
        // the day's workload is already split across crews. The actual stacking order is handled
        // by FullCalendar's own `eventOrder` option below (confirmed, via a throwaway probe page,
        // that its custom-function form does receive extendedProps). applyGrouping() re-derives
        // that same order client-side to know where to drop a divider line between buckets, then
        // marks that event's harness with a CSS class for cleaning_calendar.css to draw the line
        // above. It's re-run from a MutationObserver on the whole calendar (below, after render())
        // rather than just FullCalendar's eventsSet hook, because a busy day (more cleans than
        // dayMaxEventRows) triggers FullCalendar's own "+more" overflow pass, which rebuilds day
        // cells' event harnesses in a later, separate tick - confirmed via a throwaway probe page
        // that eventsSet alone fires too early and the divider classes it set get silently wiped
        // when that overflow pass replaces the harnesses a moment later. A structural observer
        // re-applies the classes whenever that happens, no matter when it happens.
        var eventElsById = {};

        function bucketRank(event) {
            var assigned = event.extendedProps.assigned_to || [];
            return assigned.length === 0 ? 0 : (event.extendedProps.team || 1);
        }

        function applyGrouping(events) {
            Object.keys(eventElsById).forEach(function (id) {
                var harness = eventElsById[id] && eventElsById[id].closest('.fc-daygrid-event-harness');
                if (harness) harness.classList.remove('staff-clean-group-start');
            });

            var byDate = {};
            events.forEach(function (event) {
                (byDate[event.startStr] = byDate[event.startStr] || []).push(event);
            });

            Object.keys(byDate).forEach(function (dateStr) {
                var dayEvents = byDate[dateStr].slice().sort(function (a, b) {
                    return bucketRank(a) - bucketRank(b);
                });
                var prevRank = null;
                dayEvents.forEach(function (event) {
                    var harness = eventElsById[event.id] && eventElsById[event.id].closest('.fc-daygrid-event-harness');
                    if (!harness) return;
                    var rank = bucketRank(event);
                    if (prevRank !== null && rank !== prevRank) harness.classList.add('staff-clean-group-start');
                    prevRank = rank;
                });
            });
        }

        var calendar = new FullCalendar.Calendar(el, {
            initialView: 'cleaningWeek',
            locale: 'en-gb',
            headerToolbar: {
                left: 'prev,next today',
                center: 'title',
                right: 'cleaningWeek,dayGridMonth',
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
            eventOrder: function (a, b) {
                return bucketRank(a) - bucketRank(b);
            },

            eventDidMount: function (info) {
                var props = info.event.extendedProps;
                var fallbackKey = props.location_id != null ? props.location_id : props.property_id;
                var color = props.location_color || LOCATION_COLOR_FALLBACK[fallbackKey % LOCATION_COLOR_FALLBACK.length];
                info.el.style.backgroundColor = color;
                info.el.style.borderColor = color;
                if (props.status === 'done') {
                    info.el.style.opacity = '0.55';
                }
                eventElsById[info.event.id] = info.el;
            },
            eventWillUnmount: function (info) {
                delete eventElsById[info.event.id];
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

            var teamInput = dialogBody.querySelector('[name="team"]');
            if (teamInput) params.set('team', teamInput.value);

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

        // Re-derives the divider lines any time the calendar's own DOM structure changes for any
        // reason (a fetch, a drag-drop, the "+more" overflow pass) - see the comment above
        // eventElsById for why this can't just be done once from an eventsSet/eventDrop hook.
        // Only watches childList/subtree (element added/removed), never attributes, so our own
        // classList.add/remove calls below don't re-trigger it.
        new MutationObserver(function () {
            applyGrouping(calendar.getEvents());
        }).observe(el, { childList: true, subtree: true });
    });
})();
