(function () {
    "use strict";

    var comissoes = window.FIN_COMISSOES || { servico: 60, produto: 10 };
    var grafico = window.FIN_GRAFICO || { labels: [], valores: [] };

    function parseValor(raw) {
        if (!raw) return 0;
        var s = String(raw).trim().replace(",", ".");
        var n = parseFloat(s);
        return isNaN(n) ? 0 : n;
    }

    function calcularSplit(valor, categoria) {
        var pct = categoria === "Produto" ? comissoes.produto : comissoes.servico;
        if (categoria === "Despesa") {
            return {
                bruto: valor,
                comissao: 0,
                retencao: valor,
                pct: 0,
            };
        }
        var comissao = Math.round(valor * pct) / 100;
        return {
            bruto: valor,
            comissao: comissao,
            retencao: Math.round((valor - comissao) * 100) / 100,
            pct: pct,
        };
    }

    function atualizarSplitPreview() {
        var el = document.getElementById("splitPreview");
        var valorInput = document.getElementById("lancValor");
        var catSelect = document.getElementById("lancCategoria");
        var profSelect = document.getElementById("lancProfissional");
        if (!el || !valorInput || !catSelect) return;

        var valor = parseValor(valorInput.value);
        var cat = catSelect.value;
        if (valor <= 0 || cat === "Despesa") {
            el.classList.add("d-none");
            return;
        }
        var s = calcularSplit(valor, cat);
        var profNome =
            profSelect && profSelect.selectedIndex > 0
                ? profSelect.options[profSelect.selectedIndex].text
                : "—";
        el.classList.remove("d-none");
        el.innerHTML =
            "<strong>Split sugerido</strong> (" +
            profNome +
            ") · " +
            s.pct +
            "%<br>" +
            "Bruto: <strong>R$ " +
            s.bruto.toFixed(2) +
            "</strong> · Comissão profissional: <strong>R$ " +
            s.comissao.toFixed(2) +
            "</strong> · Retenção casa: <strong>R$ " +
            s.retencao.toFixed(2) +
            "</strong>";
    }

    function initChart() {
        var canvas = document.getElementById("chartFaturamento");
        if (!canvas || typeof Chart === "undefined") return;
        var ctx = canvas.getContext("2d");
        new Chart(ctx, {
            type: "line",
            data: {
                labels: grafico.labels || [],
                datasets: [
                    {
                        label: "Receitas (R$)",
                        data: grafico.valores || [],
                        borderColor: "#1e88e5",
                        backgroundColor: "rgba(30, 136, 229, 0.12)",
                        fill: true,
                        tension: 0.35,
                        pointRadius: 4,
                        pointBackgroundColor: "#1565c0",
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            callback: function (v) {
                                return "R$ " + v;
                            },
                        },
                    },
                },
            },
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        ["lancValor", "lancCategoria", "lancProfissional"].forEach(function (id) {
            var node = document.getElementById(id);
            if (node) {
                node.addEventListener("input", atualizarSplitPreview);
                node.addEventListener("change", atualizarSplitPreview);
            }
        });
        atualizarSplitPreview();
        initChart();
    });
})();
