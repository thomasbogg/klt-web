const POLL_INTERVAL_MS = 7000;

function formatRemaining(ms) {
    const totalSeconds = Math.max(0, Math.floor(ms / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

document.addEventListener('DOMContentLoaded', () => {
    const countdown = document.getElementById('pay-countdown');
    if (!countdown) return;

    const expiresAt = new Date(countdown.dataset.expiresAt).getTime();
    const statusUrl = countdown.dataset.statusUrl;
    const timeEl = document.getElementById('pay-countdown-time');

    // Client clock only drives the visible ticker - the server remains authoritative for whether
    // the hold has actually expired (reload once the countdown reaches zero, and let the server
    // re-check against its own clock rather than trusting this one, which can drift).
    let reloadedOnExpiry = false;
    const tick = () => {
        const remaining = expiresAt - Date.now();
        if (timeEl) timeEl.textContent = formatRemaining(remaining);
        if (remaining <= 0 && !reloadedOnExpiry) {
            reloadedOnExpiry = true;
            window.location.reload();
        }
    };
    tick();
    setInterval(tick, 1000);

    const poll = async () => {
        try {
            const response = await fetch(statusUrl);
            if (!response.ok) return;
            const data = await response.json();
            if (data.status !== 'pending' && data.status !== 'in_progress') {
                // Paid, declined, failed, or cancelled - reload and let the server render the
                // correct next state (confirmation redirect, or the hold-expired message).
                window.location.reload();
            }
        } catch (error) {
            // Network hiccup - just try again on the next interval.
        }
    };
    setInterval(poll, POLL_INTERVAL_MS);
});
