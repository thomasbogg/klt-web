// One-off behaviour for the booking detail page's Cancel booking button - same lightweight
// delegated-listener style as settings.js/property_detail.js (no framework, this app's whole JS
// footprint is a handful of small self-contained scripts).
document.addEventListener('submit', function (event) {
    var form = event.target;
    var message = form.dataset.confirmMessage;
    if (!message) return;
    if (!window.confirm(message)) {
        event.preventDefault();
    }
});

// Update history starts collapsed to its 5 newest rows (older ones carry
// .staff-task-history-row-extra + .details-section-hidden) so a booking with many staff edits
// doesn't push the rest of the page down - "Show more updates" reveals the rest in place.
var showMoreHistoryButton = document.getElementById('staff-show-more-history');
if (showMoreHistoryButton) {
    showMoreHistoryButton.addEventListener('click', function () {
        document.querySelectorAll('.staff-task-history-row-extra').forEach(function (row) {
            row.classList.remove('details-section-hidden');
        });
        showMoreHistoryButton.classList.add('details-section-hidden');
    });
}
