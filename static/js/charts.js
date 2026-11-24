/* ACCOUNTING PRO chart theme helpers */
(function () {
  "use strict";
  if (typeof Chart === "undefined") return;

  Chart.defaults.font.family = "Inter, Segoe UI, system-ui, sans-serif";
  Chart.defaults.font.size = 11;
  Chart.defaults.color = "#6B7280";
  Chart.defaults.plugins.legend.labels.usePointStyle = true;
  Chart.defaults.plugins.legend.labels.boxWidth = 8;
  Chart.defaults.plugins.legend.labels.padding = 14;
  Chart.defaults.maintainAspectRatio = false;

  window.AP = window.AP || {};

  window.AP.money = function (value) {
    const n = Number(value || 0);
    return "Rs. " + n.toLocaleString(undefined, {
      minimumFractionDigits: 0, maximumFractionDigits: 0
    });
  };

  window.AP.gradient = function (ctx, hex) {
    const chart = ctx.chart;
    const { ctx: canvas, chartArea } = chart;
    if (!chartArea) return hex;
    const g = canvas.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
    g.addColorStop(0, hex + "cc");
    g.addColorStop(1, hex + "05");
    return g;
  };

  window.AP.tooltip = {
    backgroundColor: "#101D3A",
    padding: 10,
    cornerRadius: 8,
    titleFont: { size: 11.5, weight: "600" },
    bodyFont: { size: 11.5 },
    displayColors: true,
    callbacks: {
      label: function (c) {
        const label = c.dataset.label ? c.dataset.label + ": " : "";
        return label + window.AP.money(c.parsed.y !== undefined ? c.parsed.y : c.parsed);
      }
    }
  };

  window.AP.grid = {
    color: "#EEF1F7",
    drawBorder: false
  };

  window.AP.axis = {
    grid: window.AP.grid,
    ticks: { font: { size: 10.5 } },
    border: { display: false }
  };

  window.AP.yAxis = {
    grid: window.AP.grid,
    border: { display: false },
    ticks: {
      font: { size: 10.5 },
      callback: function (value) {
        if (Math.abs(value) >= 1000000) return (value / 1000000).toFixed(1) + "M";
        if (Math.abs(value) >= 1000) return (value / 1000).toFixed(0) + "k";
        return value;
      }
    }
  };
})();
