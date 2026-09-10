document.addEventListener('DOMContentLoaded', function () {
    var ownerSelect = document.querySelector('.staff-owner-filter-select');
    var propertySelect = document.querySelector('.staff-property-filter-select');
    if (!ownerSelect || !propertySelect) return;

    function applyFilter() {
        var ownerId = ownerSelect.value;
        var selectedStillValid = false;
        Array.prototype.forEach.call(propertySelect.options, function (option) {
            if (!option.value) return;
            var matches = !ownerId || option.dataset.ownerId === ownerId;
            option.hidden = !matches;
            if (matches && option.selected) selectedStillValid = true;
        });
        if (ownerId && !selectedStillValid) propertySelect.value = '';
    }

    ownerSelect.addEventListener('change', applyFilter);
    applyFilter();
});
