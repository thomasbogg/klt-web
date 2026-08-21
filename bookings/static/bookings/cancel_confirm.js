// Cancel Booking confirm page - the submit button stays disabled until the guest has retyped
// their own booking reference exactly (case-insensitive, whitespace trimmed), the same
// type-to-confirm pattern used for any other genuinely irreversible action.
const input = document.getElementById('reference_confirm');
const submitButton = document.getElementById('cancel-confirm-submit');

function updateSubmitState() {
    if (!input || !submitButton) return;
    const typed = input.value.trim().toUpperCase();
    const expected = (input.dataset.reference || '').trim().toUpperCase();
    submitButton.disabled = !expected || typed !== expected;
}

if (input && submitButton) {
    input.addEventListener('input', updateSubmitState);
    updateSubmitState();
}
