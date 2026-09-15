// Only the lead (first) guest of the whole stay is asked whether they have a Portuguese NIF - per
// Thomas, that single answer governs every guest across every apartment: "yes" means nobody
// registers at all (not even the lead guest's own full form), "no" reveals the lead guest's full
// form *and* every other guest's own section. Same show/hide-and-disable pattern as
// arrival_departure.js's wireMethodSelect() - a hidden field is also disabled, so it can neither
// block submission nor reach request.POST.
//
// Scoped per <form>, not per document, purely so a page with more than one
// form.guest-registrations-form (there isn't one today) would keep each independent - within a
// form there is exactly ONE NIF question now (2026-09-16: a multi-property stay's guests read as
// one continuous sequence with a single combined Save button, so `nifRadios[0]` is always the
// true stay-wide lead, never a second apartment's own), so no further grouping is needed.
document.querySelectorAll('form.guest-registrations-form').forEach((form) => {
    const nifRadios = form.querySelectorAll('.guest-registration-nif-radio');
    if (!nifRadios.length) return;

    const leadSection = nifRadios[0].closest('.guest-registration-section');
    const leadGroups = leadSection.querySelectorAll('[data-nif-answer]');
    const otherSections = Array.from(form.querySelectorAll('.guest-registration-section'))
        .filter((section) => section !== leadSection);

    function toggle(element, show) {
        element.classList.toggle('details-section-hidden', !show);
        element.querySelectorAll('input, select, textarea').forEach((field) => {
            field.disabled = !show;
        });
    }

    function update() {
        const checked = leadSection.querySelector('.guest-registration-nif-radio:checked');
        const answer = checked ? checked.value : null;
        leadGroups.forEach((group) => toggle(group, group.dataset.nifAnswer === answer));
        otherSections.forEach((section) => toggle(section, answer === 'no'));
    }

    nifRadios.forEach((radio) => radio.addEventListener('change', update));
    update();
});
