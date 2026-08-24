// Only the lead (first) guest is asked whether they have a Portuguese NIF - per Thomas, that
// single answer governs the whole party: "yes" means nobody registers at all (not even the lead
// guest's own full form), "no" reveals the lead guest's full form *and* every other guest's own
// section. Same show/hide-and-disable pattern as arrival_departure.js's wireMethodSelect() - a
// hidden field is also disabled, so it can neither block submission nor reach request.POST.
const nifRadios = document.querySelectorAll('.guest-registration-nif-radio');

if (nifRadios.length) {
    const leadSection = nifRadios[0].closest('.guest-registration-section');
    const leadGroups = leadSection.querySelectorAll('[data-nif-answer]');
    const otherSections = Array.from(document.querySelectorAll('.guest-registration-section'))
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
}
