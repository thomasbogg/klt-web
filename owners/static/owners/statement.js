document.addEventListener('DOMContentLoaded', function () {
    // Toggles the bank-details row under a CLEANS_INFORMAL_MONTHLY "Pay this now" button - no
    // outbound link for this one (see finance/services.py::owner_payable_invoices' own docstring
    // on why the old Wise link was dropped), just an in-page reveal.
    document.querySelectorAll('.owner-statement-pay-toggle').forEach(function (button) {
        button.addEventListener('click', function () {
            var target = document.getElementById(button.dataset.target);
            if (target) target.hidden = !target.hidden;
        });
    });
});
