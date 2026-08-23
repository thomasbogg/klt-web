document.addEventListener('DOMContentLoaded', function () {
    var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]').value;

    function panelFor(model) {
        return document.querySelector('.staff-quick-add-panel[data-quick-add="' + model + '"]');
    }

    function submitPanel(panel) {
        var model = panel.dataset.quickAdd;
        var select = document.querySelector('select[name="' + model + '"]');
        var errorBox = panel.querySelector('.staff-quick-add-error');
        var fields = panel.querySelectorAll('[data-field]');

        var formData = new FormData();
        formData.append('csrfmiddlewaretoken', csrfToken);
        fields.forEach(function (input) {
            var value = input.type === 'checkbox' ? (input.checked ? 'on' : '') : input.value;
            formData.append(input.dataset.field, value);
        });

        errorBox.textContent = '';
        fetch(panel.dataset.endpoint, { method: 'POST', body: formData })
            .then(function (response) {
                return response.json().then(function (data) {
                    return { ok: response.ok, data: data };
                });
            })
            .then(function (result) {
                if (!result.ok) {
                    errorBox.textContent = result.data.error || 'Could not add - please check the fields.';
                    return;
                }
                var option = document.createElement('option');
                option.value = result.data.id;
                option.textContent = result.data.label;
                option.selected = true;
                select.appendChild(option);

                fields.forEach(function (input) {
                    if (input.type === 'checkbox') input.checked = false; else input.value = '';
                });
                panel.classList.remove('staff-quick-add-open');
            })
            .catch(function () {
                errorBox.textContent = 'Something went wrong - please try again.';
            });
    }

    document.addEventListener('click', function (event) {
        var toggle = event.target.closest('.staff-quick-add-toggle');
        if (toggle) {
            var panel = panelFor(toggle.dataset.target);
            if (panel) panel.classList.toggle('staff-quick-add-open');
            return;
        }

        var submit = event.target.closest('.staff-quick-add-submit');
        if (submit) {
            submitPanel(submit.closest('.staff-quick-add-panel'));
        }
    });

    // Quick-add panel inputs live inside the main Create Property <form>, so an Enter keypress
    // would otherwise submit that outer form instead of just this panel.
    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter') return;
        var panel = event.target.closest('.staff-quick-add-panel');
        if (!panel) return;
        event.preventDefault();
        submitPanel(panel);
    });
});
