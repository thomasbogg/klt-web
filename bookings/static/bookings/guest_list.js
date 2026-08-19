const rowsContainer = document.getElementById('guest-rows');
const rowTemplate = document.getElementById('guest-row-template');
const addButton = document.getElementById('guest-row-add');
const priceChangeOverlay = document.getElementById('price-change-overlay');
const priceChangeEditButton = document.getElementById('price-change-edit');

function renumberRows() {
    const rows = rowsContainer.querySelectorAll('.guest-row:not(.guest-row-header)');
    rows.forEach((row, index) => {
        const indexLabel = row.querySelector('.guest-row-index');
        if (indexLabel) {
            indexLabel.textContent = index + 1;
        }
    });
}

if (addButton && rowTemplate && rowsContainer) {
    addButton.addEventListener('click', () => {
        rowsContainer.appendChild(rowTemplate.content.cloneNode(true));
        renumberRows();
    });
}

if (rowsContainer) {
    rowsContainer.addEventListener('click', (event) => {
        if (event.target.matches('.guest-row-remove')) {
            event.target.closest('.guest-row').remove();
            renumberRows();
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
}

if (priceChangeEditButton && priceChangeOverlay) {
    priceChangeEditButton.addEventListener('click', () => {
        priceChangeOverlay.remove();
    });
}
