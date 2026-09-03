document.addEventListener('click', function (event) {
    var button = event.target.closest('[data-copy-target]');
    if (!button) return;
    var input = document.getElementById(button.getAttribute('data-copy-target'));
    if (!input) return;

    var showCopied = function () {
        var original = button.textContent;
        button.textContent = 'Copied!';
        setTimeout(function () { button.textContent = original; }, 1500);
    };

    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(input.value).then(showCopied);
    } else {
        input.select();
        document.execCommand('copy');
        showCopied();
    }
});

document.addEventListener('submit', function (event) {
    var form = event.target.closest('[data-ical-sync-popup]');
    if (!form) return;
    window.open('', form.target, 'width=700,height=600,scrollbars=yes,resizable=yes');
});

// Same named-window pattern as the iCal sync popup above, for a plain GET link instead of a
// POST form (e.g. Rate card's "Platform rates") - opens/focuses the target window before the
// link's own default navigation follows into it.
document.addEventListener('click', function (event) {
    var link = event.target.closest('[data-popup-link]');
    if (!link) return;
    window.open('', link.target, 'width=800,height=600,scrollbars=yes,resizable=yes');
});
