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
