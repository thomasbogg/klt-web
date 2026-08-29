(function () {
    // Same fallback palette as cleaning_calendar.js, for consistency when a location has no
    // curated Location.color yet.
    var LOCATION_COLOR_FALLBACK = [
        '#4C6EF5', '#12B886', '#F76707', '#BE4BDB', '#1098AD', '#E64980', '#F59F00', '#495057',
    ];
    var NO_MEET_GREET_COLOR = '#c7c7c7';
    // Only key_box/welcome_visit get a tile icon - arrival is the default, most-common task type
    // and already reads clearly as "guest name, time" on its own.
    var TASK_TYPE_ICON = {
        key_box: '/static/staff/icons/checkin-key.svg',
        welcome_visit: '/static/staff/icons/checkin-welcome.svg',
    };

    function getCookie(name) {
        var match = document.cookie.match('(^|;)\\s*' + name + '\\s*=\\s*([^;]+)');
        return match ? decodeURIComponent(match[2]) : null;
    }

    function showError(message) {
        window.alert(message);
    }

    document.addEventListener('DOMContentLoaded', function () {
        var el = document.getElementById('checkins-calendar');
        if (!el) return;

        var eventsUrl = el.dataset.eventsUrl;
        var moveUrlTemplate = el.dataset.moveUrlTemplate;
        var detailUrlTemplate = el.dataset.detailUrlTemplate;
        var toggleDoneUrlTemplate = el.dataset.toggleDoneUrlTemplate;
        var saveUrlTemplate = el.dataset.saveUrlTemplate;
        var csrftoken = getCookie('csrftoken');

        var dialog = document.getElementById('checkin-dialog');
        var dialogBody = document.getElementById('checkin-dialog-body');
        var currentCheckinId = null;

        document.getElementById('checkin-dialog-close').addEventListener('click', function () {
            dialog.close();
        });
        document.getElementById('checkin-dialog-toggle-done').addEventListener('click', function () {
            if (currentCheckinId != null) toggleDone(currentCheckinId);
        });
        // Extras/deposit checkboxes only exist inside an 'arrival' popup's freshly-rendered HTML
        // - delegate from the static dialog body rather than re-binding on every eventClick.
        dialogBody.addEventListener('change', function (event) {
            if (currentCheckinId == null) return;
            var id = event.target.id;
            if (id === 'checkin-extras-collected' || id === 'checkin-deposit-collected') {
                saveCheckboxes(currentCheckinId);
            }
        });

        // Visual drag hinting: shade every day column that isn't the dragged event's own day -
        // same role as cleaning_calendar.js's shadeInvalidDays/clearShadedDays, adapted for
        // timeGrid's column layout (a check-in's date is never draggable, only its time-of-day
        // within that same date - see StaffCheckinMoveView's own docstring). The actual rejection
        // is still enforced authoritatively in eventDrop below, with an explicit message - this
        // is only the free mid-drag visual aid.
        var shadedCols = [];

        function shadeOtherDays(ownDateStr) {
            el.querySelectorAll('.fc-timegrid-col[data-date]').forEach(function (col) {
                if (col.getAttribute('data-date') !== ownDateStr) {
                    col.classList.add('staff-checkin-invalid-day');
                    shadedCols.push(col);
                }
            });
        }

        function clearShadedDays() {
            shadedCols.forEach(function (col) { col.classList.remove('staff-checkin-invalid-day'); });
            shadedCols = [];
        }

        var calendar = new FullCalendar.Calendar(el, {
            initialView: 'timeGridWeek',
            locale: 'en-gb',
            headerToolbar: {
                left: 'prev,next today',
                center: 'title',
                right: 'timeGridWeek,timeGridDay',
            },
            editable: true,
            eventStartEditable: true,
            eventDurationEditable: false,
            // The title already carries the real time (StaffCheckinEventsView's title string) -
            // FullCalendar's own default time prefix reads as if it were the event's position on
            // the hour axis, which for a staggered/collision-nudged event (see
            // defaultTimedEventDuration below) it deliberately isn't.
            displayEventTime: false,
            // A short, consistent block height for every event (rather than the 1-hour default
            // an end-less event would otherwise get) - prevents unrelated arrivals apart from
            // falsely reading as overlapping and being split side-by-side by FullCalendar's own
            // layout engine. Close-together checkins are pre-staggered server-side
            // (StaffCheckinEventsView.EVENT_BLOCK_MINUTES, currently 30) into back-to-back slots -
            // deliberately 1 minute short of that same 30 here, not equal to it, so adjacent
            // blocks render with a real visual gap instead of sitting flush against each other
            // (which still reads as one touching/overlapping mass - 2026-08-28, per Thomas).
            defaultTimedEventDuration: '00:29',
            allDaySlot: true,
            events: eventsUrl,

            eventDidMount: function (info) {
                var props = info.event.extendedProps;
                var fallbackKey = props.location_id != null ? props.location_id : props.property_id;
                var color = props.meet_greet === false
                    ? NO_MEET_GREET_COLOR
                    : (props.location_color || LOCATION_COLOR_FALLBACK[fallbackKey % LOCATION_COLOR_FALLBACK.length]);
                info.el.style.backgroundColor = color;
                info.el.style.borderColor = color;
                if (props.status === 'done') {
                    info.el.style.opacity = '0.55';
                }

                var iconSrc = TASK_TYPE_ICON[props.task_type];
                if (iconSrc) {
                    var titleEl = info.el.querySelector('.fc-event-title');
                    if (titleEl) {
                        var icon = document.createElement('img');
                        icon.src = iconSrc;
                        icon.alt = props.task_type;
                        icon.className = 'staff-checkin-task-icon';
                        titleEl.prepend(icon);
                    }
                }
            },

            eventDragStart: function (info) {
                shadeOtherDays(info.event.startStr.split('T')[0]);
            },
            eventDragStop: function () {
                clearShadedDays();
            },

            eventDrop: function (info) {
                var dateChanged = info.event.start.toDateString() !== info.oldEvent.start.toDateString();
                var allDayChanged = info.event.allDay !== info.oldEvent.allDay;
                if (dateChanged) {
                    showError("Arrival date changes have to be made from the booking's page, not here.");
                    info.revert();
                    return;
                }
                if (allDayChanged) {
                    showError('This arrival has no time on file - add one from the booking\'s page first.');
                    info.revert();
                    return;
                }

                var moveUrl = moveUrlTemplate.replace('0', info.event.id);
                var hh = String(info.event.start.getHours()).padStart(2, '0');
                var mm = String(info.event.start.getMinutes()).padStart(2, '0');
                fetch(moveUrl, {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrftoken,
                        'Content-Type': 'application/x-www-form-urlencoded',
                    },
                    body: 'time=' + encodeURIComponent(hh + ':' + mm),
                }).then(function (response) {
                    if (!response.ok) {
                        var isJson = (response.headers.get('Content-Type') || '').indexOf('application/json') !== -1;
                        return (isJson ? response.json() : Promise.resolve({})).then(function (data) {
                            throw new Error(data.error || 'Could not move this check-in.');
                        });
                    }
                }).catch(function (err) {
                    showError(err.message);
                    info.revert();
                });
            },

            eventClick: function (info) {
                var checkinId = info.event.id;
                var detailUrl = detailUrlTemplate.replace('0', checkinId);
                fetch(detailUrl).then(function (response) {
                    return response.json();
                }).then(function (data) {
                    if (data.error) {
                        showError(data.error);
                        return;
                    }
                    dialogBody.innerHTML = data.popup_html;
                    currentCheckinId = checkinId;
                    updateToggleButtonLabel();
                    dialog.showModal();
                }).catch(function () {
                    showError('Could not load this check-in.');
                });
            },
        });

        function updateToggleButtonLabel() {
            var wrapper = dialogBody.querySelector('.staff-checkin-popup');
            var button = document.getElementById('checkin-dialog-toggle-done');
            if (!wrapper || !button) return;
            button.textContent = wrapper.dataset.status === 'done' ? 'Mark as not done' : 'Mark as done';
        }

        function toggleDone(checkinId) {
            var url = toggleDoneUrlTemplate.replace('0', checkinId);
            fetch(url, {
                method: 'POST',
                headers: { 'X-CSRFToken': csrftoken },
            }).then(function (response) {
                return response.json();
            }).then(function (data) {
                if (data.error) {
                    showError(data.error);
                    return;
                }
                var wrapper = dialogBody.querySelector('.staff-checkin-popup');
                if (wrapper) wrapper.dataset.status = data.status;
                updateToggleButtonLabel();
                calendar.refetchEvents();
            }).catch(function () {
                showError('Could not update this check-in.');
            });
        }

        function saveCheckboxes(checkinId) {
            var url = saveUrlTemplate.replace('0', checkinId);
            var params = new URLSearchParams();
            var extras = dialogBody.querySelector('#checkin-extras-collected');
            var depositCollected = dialogBody.querySelector('#checkin-deposit-collected');
            params.set('extras_collected', extras && extras.checked ? 'true' : 'false');
            params.set('deposit_collected', depositCollected && depositCollected.checked ? 'true' : 'false');

            fetch(url, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': csrftoken,
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: params.toString(),
            }).catch(function () {
                showError('Could not save this check-in.');
            });
        }

        calendar.render();
    });
})();
