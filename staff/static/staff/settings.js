// Two small, self-contained behaviours for the Settings > People tables - kept as one delegated
// listener each, same lightweight style as property_detail.js's copy button and
// property_create.js's quick-add toggle (no framework/bundler, this app's whole JS footprint is a
// handful of one-off scripts like this).

document.addEventListener('click', function (event) {
    var toggle = event.target.closest('.staff-row-expand-toggle');
    if (!toggle) return;
    var target = document.getElementById(toggle.dataset.target);
    if (target) {
        target.hidden = !target.hidden;
    }
});

// Property.owner/manager/accountant are all SET_NULL, not PROTECT, so deleting one of these rows
// silently orphans any property still pointing at it rather than erroring - warn first using the
// live property_count each row already carries (see StaffSettingsView._context).
document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form.classList.contains('staff-confirm-delete')) return;
    var count = parseInt(form.dataset.propertyCount || '0', 10);
    if (count <= 0) return;
    var noun = count === 1 ? 'property' : 'properties';
    var subject = count === 1 ? 'that property' : 'those properties';
    var role = form.dataset.roleLabel || 'entry';
    var label = form.dataset.entityLabel || 'This';
    var confirmed = window.confirm(
        label + ' is currently assigned to ' + count + ' ' + noun + '. Deleting it will leave ' +
        subject + ' with no ' + role + ' until reassigned. Delete anyway?'
    );
    if (!confirmed) {
        event.preventDefault();
    }
});
