const sidebar = document.querySelector("[data-sidebar]");
const backdrop = document.querySelector("[data-sidebar-backdrop]");
const menuButtons = document.querySelectorAll("[data-menu-toggle]");

function setSidebarOpen(open) {
  if (!sidebar || !backdrop || menuButtons.length === 0) {
    return;
  }

  sidebar.dataset.open = String(open);
  sidebar.classList.toggle("-translate-x-full", !open);
  backdrop.classList.toggle("hidden", !open);
  menuButtons.forEach((button) => button.setAttribute("aria-expanded", String(open)));
  document.body.classList.toggle("overflow-hidden", open);
}

menuButtons.forEach((button) => {
  button.addEventListener("click", () => {
    setSidebarOpen(sidebar?.dataset.open !== "true");
  });
});

backdrop?.addEventListener("click", () => setSidebarOpen(false));

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    setSidebarOpen(false);
  }
});

window.addEventListener("resize", () => {
  if (window.matchMedia("(min-width: 1024px)").matches) {
    setSidebarOpen(false);
  }
});

document.querySelectorAll("[data-password-toggle]").forEach((button) => {
  button.addEventListener("click", () => {
    const inputId = button.getAttribute("aria-controls");
    const input = inputId ? document.getElementById(inputId) : null;
    if (!(input instanceof HTMLInputElement)) {
      return;
    }

    const showingPassword = input.type === "text";
    input.type = showingPassword ? "password" : "text";
    const label = showingPassword ? "Mostrar senha" : "Ocultar senha";
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
    button.setAttribute("aria-pressed", String(!showingPassword));
  });
});

const chartTextColor = "#9aa2ad";
const chartGridColor = "rgba(58, 65, 76, 0.45)";

function formatRate(value) {
  const units = ["B/s", "KiB/s", "MiB/s", "GiB/s"];
  let amount = Number(value);
  let unitIndex = 0;
  while (amount >= 1024 && unitIndex < units.length - 1) {
    amount /= 1024;
    unitIndex += 1;
  }
  const precision = unitIndex === 0 ? 0 : 1;
  return `${amount.toFixed(precision)} ${units[unitIndex]}`;
}

function commonChartOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { intersect: false, mode: "index" },
    plugins: {
      legend: {
        labels: { color: chartTextColor, boxWidth: 10, boxHeight: 10 },
      },
    },
    scales: {
      x: {
        grid: { display: false },
        ticks: {
          color: chartTextColor,
          maxTicksLimit: 6,
          callback(_value, index) {
            const label = this.getLabelForValue(index);
            return new Date(label).toLocaleTimeString("pt-BR", {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            });
          },
        },
      },
      y: {
        beginAtZero: true,
        grid: { color: chartGridColor },
        ticks: { color: chartTextColor },
      },
    },
  };
}

function initializeMetricsCharts(root = document) {
  const dataElement = root.querySelector("[data-metrics-chart-data]");
  if (!dataElement || typeof window.Chart === "undefined") {
    return;
  }

  let metrics;
  try {
    metrics = JSON.parse(dataElement.textContent);
  } catch {
    return;
  }

  const resourceCanvas = root.querySelector("[data-resource-chart]");
  if (resourceCanvas) {
    new window.Chart(resourceCanvas, {
      type: "line",
      data: {
        labels: metrics.labels,
        datasets: [
          {
            label: "CPU",
            data: metrics.cpu,
            borderColor: "#22d3ee",
            backgroundColor: "rgba(34, 211, 238, 0.12)",
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.25,
          },
          {
            label: "Memória",
            data: metrics.memory,
            borderColor: "#a78bfa",
            backgroundColor: "rgba(167, 139, 250, 0.12)",
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.25,
          },
        ],
      },
      options: {
        ...commonChartOptions(),
        scales: {
          ...commonChartOptions().scales,
          y: {
            ...commonChartOptions().scales.y,
            max: 100,
            ticks: {
              color: chartTextColor,
              callback: (value) => `${value}%`,
            },
          },
        },
      },
    });
  }

  const networkCanvas = root.querySelector("[data-network-chart]");
  if (networkCanvas) {
    new window.Chart(networkCanvas, {
      type: "line",
      data: {
        labels: metrics.labels,
        datasets: [
          {
            label: "Recebido",
            data: metrics.network_received,
            borderColor: "#34d399",
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.25,
          },
          {
            label: "Enviado",
            data: metrics.network_sent,
            borderColor: "#fbbf24",
            borderWidth: 2,
            pointRadius: 0,
            tension: 0.25,
          },
        ],
      },
      options: {
        ...commonChartOptions(),
        scales: {
          ...commonChartOptions().scales,
          y: {
            ...commonChartOptions().scales.y,
            ticks: { color: chartTextColor, callback: formatRate },
          },
        },
      },
    });
  }
}

document.body.addEventListener("htmx:beforeSwap", (event) => {
  const target = event.detail.target;
  if (!target?.matches("[data-metrics-panel]") || typeof window.Chart === "undefined") {
    return;
  }
  target.querySelectorAll("canvas").forEach((canvas) => {
    window.Chart.getChart(canvas)?.destroy();
  });
});

document.body.addEventListener("htmx:afterSwap", (event) => {
  if (event.detail.target?.matches("[data-metrics-panel]")) {
    initializeMetricsCharts(event.detail.target);
  }
});
