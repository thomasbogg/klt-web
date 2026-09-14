// Scoped per enclosing <form>, not per page (2026-09-15): a multi-property stay's merged Guest
// List renders one of these forms per apartment, so ids would silently bind every leg's
// "+ Add guest" button, row removal and price-change overlay to the FIRST leg's form. Every page
// that loads this (details.html, balance_details.html, manage_guests.html) has exactly one
// editable rows container per form, so scoping this way leaves the single-form pages behaving
// identically. The *read-only* party table on manage_guests.html deliberately carries no
// data-guest-rows marker - its own "remove" controls are real server-side POSTs
// (BookingManageGuestRemoveView), not client-side row deletion.

function renumberRows(rowsContainer) {
    const rows = rowsContainer.querySelectorAll('.guest-row:not(.guest-row-header)');
    rows.forEach((row, index) => {
        const indexLabel = row.querySelector('.guest-row-index');
        if (indexLabel) {
            indexLabel.textContent = index + 1;
        }
    });
}

document.querySelectorAll('[data-guest-rows]').forEach((rowsContainer) => {
    const scope = rowsContainer.closest('form') || document;
    const rowTemplate = scope.querySelector('[data-guest-row-template]');
    const addButton = scope.querySelector('[data-guest-row-add]');
    const priceChangeOverlay = scope.querySelector('[data-price-change-overlay]');
    const priceChangeEditButton = scope.querySelector('[data-price-change-edit]');

    if (addButton && rowTemplate) {
        addButton.addEventListener('click', () => {
            rowsContainer.appendChild(rowTemplate.content.cloneNode(true));
            renumberRows(rowsContainer);
        });
    }

    rowsContainer.addEventListener('click', (event) => {
        if (event.target.matches('.guest-row-remove')) {
            event.target.closest('.guest-row').remove();
            renumberRows(rowsContainer);
        }
    });

    // Any edit after a price-change warning is shown invalidates it - the guest must resubmit to
    // see a fresh recalculation, rather than being able to "Proceed to payment anyway" at a price
    // that no longer matches what they just typed.
    rowsContainer.addEventListener('input', () => {
        if (priceChangeOverlay) {
            priceChangeOverlay.classList.add('price-change-stale');
        }
    });

    if (priceChangeEditButton && priceChangeOverlay) {
        priceChangeEditButton.addEventListener('click', () => {
            priceChangeOverlay.remove();
        });
    }
});
