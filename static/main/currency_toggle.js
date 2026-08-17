const CURRENCY_SYMBOLS = { EUR: '€', GBP: '£' };

function applyCurrency(currency) {
    document.querySelectorAll('.price-value[data-eur]').forEach((el) => {
        const amount = currency === 'GBP' ? el.dataset.gbp : el.dataset.eur;
        if (amount === undefined) return;
        el.textContent = CURRENCY_SYMBOLS[currency] + amount;
    });

    document.querySelectorAll('.currency-toggle button').forEach((button) => {
        button.classList.toggle('active', button.dataset.currency === currency);
    });

    const hiddenInput = document.getElementById('id_currency');
    if (hiddenInput) hiddenInput.value = currency;
}

document.addEventListener('DOMContentLoaded', () => {
    const buttons = document.querySelectorAll('.currency-toggle button');
    if (!buttons.length) return;

    buttons.forEach((button) => {
        button.addEventListener('click', () => applyCurrency(button.dataset.currency));
    });

    // Re-apply whichever button is already marked active server-side, so pages that default to
    // a non-EUR currency (e.g. a confirmation page for a GBP booking) render consistently.
    const initiallyActive = document.querySelector('.currency-toggle button.active');
    applyCurrency(initiallyActive ? initiallyActive.dataset.currency : 'EUR');
});
