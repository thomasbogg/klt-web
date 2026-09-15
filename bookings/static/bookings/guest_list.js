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
    // Scoped to the enclosing <section>, NOT the <form> (2026-09-15): once every apartment's
    // guest list shares one combined form, a form-level lookup finds the FIRST leg's row
    // template, add button and limit note and binds every apartment to them. Each apartment's
    // list is its own .details-section, which is the real boundary here.
    const scope = rowsContainer.closest('section') || rowsContainer.closest('form') || document;
    const rowTemplate = scope.querySelector('[data-guest-row-template]');
    const addButton = scope.querySelector('[data-guest-row-add]');
    const limitNote = scope.querySelector('[data-guest-row-limit]');
    // The price-change overlay belongs to the SUBMISSION, not to one apartment's section - in
    // combined mode there's one overlay for the whole stay, sitting in the shared form outside
    // every section - so it keeps the wider scope on purpose.
    const formScope = rowsContainer.closest('form') || document;
    const priceChangeOverlay = formScope.querySelector('[data-price-change-overlay]');
    const priceChangeEditButton = formScope.querySelector('[data-price-change-edit]');

    // Occupancy cap (2026-09-15, per Thomas): stop the guest adding a row they'd only be told
    // about on save. Read per container, never page-wide - each apartment of a merged stay has
    // its own max_guests.
    //
    // existingGuests is 0 on the pre-balance guest list (the rows here ARE the whole party) and
    // the already-staying party size on the post-balance add form (these rows are additions on
    // top). Both are checked against the same max. Purely an affordance - the server still does
    // the real check, so a stale page or a disabled-button bypass can't over-fill a property.
    const maxGuests = parseInt(rowsContainer.dataset.maxGuests, 10);
    const existingGuests = parseInt(rowsContainer.dataset.existingGuests, 10) || 0;

    function syncCapacity() {
        if (!addButton || !Number.isFinite(maxGuests)) {
            return;
        }
        const rowCount = rowsContainer.querySelectorAll('.guest-row:not(.guest-row-header)').length;
        const atCapacity = existingGuests + rowCount >= maxGuests;
        addButton.disabled = atCapacity;
        if (limitNote) {
            limitNote.hidden = !atCapacity;
        }
    }

    if (addButton && rowTemplate) {
        addButton.addEventListener('click', () => {
            if (addButton.disabled) {
                return;
            }
            rowsContainer.appendChild(rowTemplate.content.cloneNode(true));
            renumberRows(rowsContainer);
            syncCapacity();
        });
    }

    rowsContainer.addEventListener('click', (event) => {
        if (event.target.matches('.guest-row-remove')) {
            event.target.closest('.guest-row').remove();
            renumberRows(rowsContainer);
            syncCapacity();
        }
    });

    syncCapacity();

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
