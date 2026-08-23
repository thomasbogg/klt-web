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
