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

// Property.owner/manager/accountant and StaffProfile.role are all SET_NULL, not PROTECT, so
// deleting one of these rows silently orphans whatever still points at it rather than erroring -
// warn first using the live count each row already carries (see StaffSettingsView._context).
// data-count-noun-singular/-plural default to property/properties (the original, most common
// case) but the Roles table overrides them to user/users, since data-property-count there
// actually holds a user count, not a property count.
document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form.classList.contains('staff-confirm-delete')) return;
    var count = parseInt(form.dataset.propertyCount || '0', 10);
    if (count <= 0) return;
    var noun = count === 1
        ? (form.dataset.countNounSingular || 'property')
        : (form.dataset.countNounPlural || 'properties');
    var subject = (count === 1 ? 'that ' : 'those ') + noun;
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
