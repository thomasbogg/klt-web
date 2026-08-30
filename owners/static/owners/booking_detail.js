document.addEventListener('DOMContentLoaded', function () {
    var meetGreetToggle = document.getElementById('owner-meet-greet-toggle');
    var guestPanel = document.getElementById('owner-guest-details-panel');
    if (!meetGreetToggle || !guestPanel) return;

    function sync() {
        var required = meetGreetToggle.checked;
        guestPanel.hidden = !required;
        // Disabled (not just hidden) so an unticked Meet & Greet never silently submits stale
        // guest details, and so the fields are correctly omitted from the POST body per the HTML
        // form spec - matching the same "hide AND disable" convention the staff booking detail
        // page's own arrival_departure.js already uses for its owner-booking-conditional row.
        guestPanel.querySelectorAll('input').forEach(function (field) {
            field.disabled = !required;
        });
    }

    meetGreetToggle.addEventListener('change', sync);
    sync();
});

document.addEventListener('DOMContentLoaded', function () {
    // Shows only the fields that apply to the currently-selected travel method (e.g. flight
    // number for a flight, travelling from/to for driving) - mirrors the guest-facing Manage
    // Booking hub's own arrival/departure form, which this owner-facing one had been missing
    // entirely (every field showed at once regardless of method - confirmed live 2026-08-30).
    // Deliberately toggles individual <label data-methods="..."> elements, not a wrapping row -
    // so the method select and whichever fields currently apply all sit as siblings in one
    // .owner-form-row and pack onto as few lines as the fields that are actually visible need
    // (per Thomas: "fit as many ... fields on one row as possible"), rather than each method's
    // fields being forced onto their own dedicated row even when nothing else is showing.
    document.querySelectorAll('.owner-travel-method-select').forEach(function (select) {
        var prefix = select.dataset.targetPrefix;
        var container = document.querySelector('.owner-form-row[data-prefix="' + prefix + '"]');
        if (!container) return;
        var fields = container.querySelectorAll('[data-methods]');

        function sync() {
            fields.forEach(function (field) {
                var methods = field.dataset.methods.split(' ');
                var show = methods.indexOf(select.value) !== -1;
                field.hidden = !show;
                field.querySelectorAll('input').forEach(function (input) {
                    input.disabled = !show;
                });
            });
        }

        select.addEventListener('change', sync);
        sync();
    });
});
