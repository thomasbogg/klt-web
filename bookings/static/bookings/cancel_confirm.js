// Cancel Booking confirm page - the submit button stays disabled until the guest has retyped
// their own reference exactly (case-insensitive, whitespace trimmed), the same type-to-confirm
// pattern used for any other genuinely irreversible action.
//
// On a multi-property stay the guest also picks WHICH apartments to cancel, so the button
// additionally requires at least one of those ticked (2026-09-15). The server refuses an empty
// selection either way - this just avoids inviting a round-trip that can only fail. The typed
// reference is the shared party reference there, not any one apartment's own.
const input = document.getElementById('reference_confirm');
const submitButton = document.getElementById('cancel-confirm-submit');
const legCheckboxes = document.querySelectorAll('input[name="cancel_leg"]');

function updateSubmitState() {
    if (!input || !submitButton) return;
    const typed = input.value.trim().toUpperCase();
    const expected = (input.dataset.reference || '').trim().toUpperCase();
    const referenceMatches = Boolean(expected) && typed === expected;
    // No checkboxes at all = a single-property cancellation, where there's nothing to choose.
    const anyLegChosen = legCheckboxes.length === 0
        || Array.from(legCheckboxes).some((box) => box.checked);
    submitButton.disabled = !(referenceMatches && anyLegChosen);
}

if (input && submitButton) {
    input.addEventListener('input', updateSubmitState);
    legCheckboxes.forEach((box) => box.addEventListener('change', updateSubmitState));
    updateSubmitState();
}
