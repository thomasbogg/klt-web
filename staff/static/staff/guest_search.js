document.addEventListener('DOMContentLoaded', function () {
    var input = document.getElementById('guest-search');
    var resultsList = document.getElementById('guest-search-results');
    if (!input || !resultsList) return;

    var searchUrl = input.dataset.searchUrl;
    var debounceTimer = null;

    function fillGuestFields(guest) {
        var form = input.closest('form');
        form.querySelector('[name="first_name"]').value = guest.first_name;
        form.querySelector('[name="last_name"]').value = guest.last_name;
        form.querySelector('[name="email"]').value = guest.email;
        form.querySelector('[name="phone"]').value = guest.phone;
        var countrySelect = form.querySelector('[name="country"]');
        if (countrySelect) countrySelect.value = guest.country;
        input.value = guest.label;
        hideResults();
    }

    function hideResults() {
        resultsList.hidden = true;
        resultsList.innerHTML = '';
    }

    function renderResults(guests) {
        resultsList.innerHTML = '';
        if (!guests.length) {
            hideResults();
            return;
        }
        guests.forEach(function (guest) {
            var item = document.createElement('li');
            item.className = 'staff-guest-search-result';
            item.textContent = guest.label;
            item.addEventListener('click', function () { fillGuestFields(guest); });
            resultsList.appendChild(item);
        });
        resultsList.hidden = false;
    }

    input.addEventListener('input', function () {
        var query = input.value.trim();
        clearTimeout(debounceTimer);
        if (query.length < 2) {
            hideResults();
            return;
        }
        debounceTimer = setTimeout(function () {
            fetch(searchUrl + '?q=' + encodeURIComponent(query))
                .then(function (response) { return response.json(); })
                .then(function (data) { renderResults(data.results); })
                .catch(hideResults);
        }, 250);
    });

    document.addEventListener('click', function (event) {
        if (event.target !== input && !resultsList.contains(event.target)) hideResults();
    });
});
