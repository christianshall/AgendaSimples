(function () {
    const el = document.getElementById('metricas-chart-data');
    if (!el || typeof Chart === 'undefined') return;

    let data;
    try {
        data = JSON.parse(el.textContent);
    } catch (e) {
        return;
    }

    const gold = '#d4af37';
    const goldSoft = 'rgba(212, 175, 55, 0.25)';
    const green = '#3dd68c';
    const blue = '#5b9cff';
    const grid = 'rgba(139, 139, 154, 0.15)';
    const text = '#8b8b9a';

    Chart.defaults.color = text;
    Chart.defaults.borderColor = grid;
    Chart.defaults.font.family = "'Outfit', system-ui, sans-serif";

    const baseOpts = {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
            legend: {
                labels: { usePointStyle: true, padding: 16 },
            },
        },
        scales: {
            x: { grid: { display: false } },
            y: { beginAtZero: true, grid: { color: grid } },
        },
    };

    const evoNeg = data.negocio && data.negocio.evolucao;
    if (evoNeg && evoNeg.series && evoNeg.series.length) {
        const labels = evoNeg.series.map((s) => s.label);
        const ctx = document.getElementById('chartEvolucaoNegocio');
        if (ctx) {
            new Chart(ctx, {
                type: 'bar',
                data: {
                    labels,
                    datasets: [
                        {
                            label: data.labels.agendamentos || 'Agendamentos',
                            data: evoNeg.series.map((s) => s.agendamentos),
                            backgroundColor: goldSoft,
                            borderColor: gold,
                            borderWidth: 2,
                            borderRadius: 6,
                            order: 2,
                        },
                        {
                            label: data.labels.concluidos || 'Concluídos',
                            data: evoNeg.series.map((s) => s.concluidos),
                            type: 'line',
                            borderColor: green,
                            backgroundColor: 'rgba(61, 214, 140, 0.1)',
                            fill: true,
                            tension: 0.35,
                            pointRadius: 4,
                            pointBackgroundColor: green,
                            order: 1,
                        },
                    ],
                },
                options: {
                    ...baseOpts,
                    plugins: {
                        ...baseOpts.plugins,
                        title: {
                            display: false,
                        },
                    },
                },
            });
        }

        const ctxRec = document.getElementById('chartReceitaNegocio');
        if (ctxRec) {
            new Chart(ctxRec, {
                type: 'line',
                data: {
                    labels,
                    datasets: [
                        {
                            label: data.labels.receita || 'Receita R$',
                            data: evoNeg.series.map((s) => s.receita),
                            borderColor: gold,
                            backgroundColor: 'rgba(212, 175, 55, 0.12)',
                            fill: true,
                            tension: 0.4,
                            pointRadius: 5,
                            pointHoverRadius: 7,
                        },
                    ],
                },
                options: baseOpts,
            });
        }
    }

    const pizza = data.negocio && data.negocio.status_pizza;
    if (pizza && pizza.length) {
        const ctxP = document.getElementById('chartStatusPizza');
        if (ctxP) {
            const cores = [gold, green, blue, '#ff6b6b', '#a78bfa', '#fb923c'];
            new Chart(ctxP, {
                type: 'doughnut',
                data: {
                    labels: pizza.map((p) => p.status),
                    datasets: [
                        {
                            data: pizza.map((p) => p.total),
                            backgroundColor: pizza.map((_, i) => cores[i % cores.length]),
                            borderWidth: 0,
                            hoverOffset: 8,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '62%',
                    plugins: {
                        legend: { position: 'bottom', labels: { padding: 12 } },
                    },
                },
            });
        }
    }

    const evoPlat = data.plataforma && data.plataforma.evolucao;
    if (evoPlat && evoPlat.series && evoPlat.series.length) {
        const labels = evoPlat.series.map((s) => s.label);
        const ctxPlat = document.getElementById('chartEvolucaoPlataforma');
        if (ctxPlat) {
            new Chart(ctxPlat, {
                type: 'line',
                data: {
                    labels,
                    datasets: [
                        {
                            label: data.labels.cadastros || 'Cadastros',
                            data: evoPlat.series.map((s) => s.cadastros),
                            borderColor: blue,
                            backgroundColor: 'rgba(91, 156, 255, 0.15)',
                            fill: true,
                            tension: 0.35,
                        },
                        {
                            label: data.labels.checkouts || 'Checkouts',
                            data: evoPlat.series.map((s) => s.checkouts),
                            borderColor: green,
                            backgroundColor: 'rgba(61, 214, 140, 0.1)',
                            fill: true,
                            tension: 0.35,
                        },
                    ],
                },
                options: baseOpts,
            });
        }
    }

    const maxTop = Math.max(
        1,
        ...(data.negocio && data.negocio.top_servicos
            ? data.negocio.top_servicos.map((t) => t.total)
            : [1])
    );
    document.querySelectorAll('.top-servicos .bar-fill').forEach((bar) => {
        const n = parseInt(bar.dataset.total, 10) || 0;
        requestAnimationFrame(() => {
            bar.style.width = Math.round((n / maxTop) * 100) + '%';
        });
    });
})();
