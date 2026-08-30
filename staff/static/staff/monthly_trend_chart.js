/* Renders a single Chart.js line chart from a json_script payload - shared by the Revenue and
   Stays tabs' own "since records began" growth-over-time chart, sitting below each tab's
   year-scoped table (which stays the month-vs-month comparison tool - this chart is the
   long-run trend view instead, per Thomas 2026-08-30). `series` lets each caller pick which
   field(s) of the row data to plot and onto which axis, since Revenue's two figures share one
   unit (euros) while Stays' two (arrivals, nights) don't. */
function renderTrendChart(canvasId, dataElementId, series) {
    const dataEl = document.getElementById(dataElementId);
    const canvas = document.getElementById(canvasId);
    if (!dataEl || !canvas) return;

    const rows = JSON.parse(dataEl.textContent);
    if (!rows.length) return;

    const labels = rows.map((row) => {
        const d = new Date(row.month);
        return d.toLocaleDateString('en-GB', { month: 'short', year: 'numeric', timeZone: 'UTC' });
    });

    const datasets = series.map((s) => ({
        label: s.label,
        data: rows.map((row) => parseFloat(row[s.key])),
        borderColor: s.color,
        backgroundColor: s.color,
        yAxisID: s.yAxisID || 'y',
        tension: 0.25,
        fill: false,
    }));

    const scales = { y: { beginAtZero: true, position: 'left' } };
    if (series.some((s) => s.yAxisID === 'y2')) {
        scales.y2 = { beginAtZero: true, position: 'right', grid: { drawOnChartArea: false } };
    }

    // eslint-disable-next-line no-undef
    new Chart(canvas, {
        type: 'line',
        data: { labels, datasets },
        options: { responsive: true, scales },
    });
}
