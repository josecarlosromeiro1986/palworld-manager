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

function initializeLogViewer(root = document) {
  const viewer = root.querySelector("[data-log-viewer]");
  if (!viewer || viewer.dataset.initialized === "true") {
    return;
  }
  viewer.dataset.initialized = "true";

  const list = viewer.querySelector("[data-log-list]");
  const viewport = viewer.querySelector("[data-log-viewport]");
  const search = viewer.querySelector("[data-log-search]");
  const categoryFilter = viewer.querySelector("[data-log-category-filter]");
  const pauseButton = viewer.querySelector("[data-log-pause]");
  const autoscroll = viewer.querySelector("[data-log-autoscroll]");
  const copyButton = viewer.querySelector("[data-log-copy]");
  const copyFeedback = viewer.querySelector("[data-copy-feedback]");
  const summary = viewer.querySelector("[data-log-summary]");
  const empty = viewer.querySelector("[data-log-empty]");
  const streamStatus = viewer.querySelector("[data-stream-status]");
  if (!list || !viewport) {
    return;
  }

  let paused = false;
  const pendingEntries = [];
  const maxEntries = Number.parseInt(viewer.dataset.maxEntries || "1000", 10);
  const categoryLabels = {
    ERROR: "ERRO",
    WARNING: "AVISO",
    CONNECTION: "CONEXÃO",
    SYSTEM: "SISTEMA",
    NORMAL: "NORMAL",
  };

  function localizedTime(isoTimestamp) {
    const date = new Date(isoTimestamp);
    if (Number.isNaN(date.getTime())) {
      return "--:--:--";
    }
    return date.toLocaleTimeString("pt-BR", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function localizeExistingTimes() {
    viewer.querySelectorAll("[data-log-time]").forEach((element) => {
      const timestamp = element.getAttribute("datetime");
      if (timestamp) {
        element.textContent = localizedTime(timestamp);
      }
    });
  }

  function updateFilters() {
    const query = (search?.value || "").trim().toLocaleLowerCase("pt-BR");
    const category = categoryFilter?.value || "ALL";
    let visibleCount = 0;
    list.querySelectorAll("[data-log-entry]").forEach((entry) => {
      const categoryMatches = category === "ALL" || entry.dataset.logCategory === category;
      const textMatches = !query || (entry.dataset.logMessage || "").includes(query);
      const visible = categoryMatches && textMatches;
      entry.classList.toggle("hidden", !visible);
      if (visible) {
        visibleCount += 1;
      }
    });
    empty?.classList.toggle("hidden", visibleCount !== 0);
    if (summary) {
      const pendingLabel = pendingEntries.length ? ` • ${pendingEntries.length} em pausa` : "";
      summary.textContent = `${visibleCount} registros exibidos${pendingLabel}`;
    }
  }

  function createEntry(payload) {
    const entry = document.createElement("li");
    entry.dataset.logEntry = "";
    entry.dataset.logCategory = payload.category;
    entry.dataset.logMessage = payload.message.toLocaleLowerCase("pt-BR");
    entry.className =
      "log-entry grid grid-cols-[5.75rem_5.5rem_minmax(0,1fr)] gap-2 border-l-2 px-2 py-1";

    const timestamp = document.createElement("time");
    timestamp.dateTime = payload.occurred_at;
    timestamp.dataset.logTime = "";
    timestamp.className = "text-muted";
    timestamp.textContent = localizedTime(payload.occurred_at);

    const category = document.createElement("span");
    category.className = "log-category";
    category.textContent = categoryLabels[payload.category] || payload.category;

    const message = document.createElement("span");
    message.dataset.logMessageText = "";
    message.className = "min-w-0 whitespace-pre-wrap break-words text-ink";
    message.textContent = payload.message;
    entry.append(timestamp, category, message);
    return entry;
  }

  function appendPayload(payload) {
    list.append(createEntry(payload));
    while (list.children.length > maxEntries) {
      list.firstElementChild?.remove();
    }
    updateFilters();
    if (autoscroll?.checked) {
      viewport.scrollTop = viewport.scrollHeight;
    }
  }

  function receivePayload(payload) {
    if (
      typeof payload?.occurred_at !== "string" ||
      typeof payload?.message !== "string" ||
      typeof payload?.category !== "string"
    ) {
      return;
    }
    if (paused) {
      pendingEntries.push(payload);
      if (pendingEntries.length > maxEntries) {
        pendingEntries.shift();
      }
      updateFilters();
      return;
    }
    appendPayload(payload);
  }

  search?.addEventListener("input", updateFilters);
  categoryFilter?.addEventListener("change", updateFilters);
  pauseButton?.addEventListener("click", () => {
    paused = !paused;
    pauseButton.setAttribute("aria-pressed", String(paused));
    pauseButton.textContent = paused ? "Retomar" : "Pausar";
    if (!paused) {
      pendingEntries.splice(0).forEach(appendPayload);
    }
    updateFilters();
  });

  copyButton?.addEventListener("click", async () => {
    const text = [...list.querySelectorAll("[data-log-entry]:not(.hidden)")]
      .map((entry) => {
        const timestamp = entry.querySelector("[data-log-time]")?.textContent || "";
        const technicalCategory = entry.dataset.logCategory || "NORMAL";
        const category = categoryLabels[technicalCategory] || technicalCategory;
        const message = entry.querySelector("[data-log-message-text]")?.textContent || "";
        return `${timestamp} ${category} ${message}`.trim();
      })
      .join("\n");
    if (!text) {
      if (copyFeedback) {
        copyFeedback.textContent = "Nenhum registro visível para copiar.";
      }
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      if (copyFeedback) {
        copyFeedback.textContent = "Trecho visível copiado.";
      }
    } catch {
      if (copyFeedback) {
        copyFeedback.textContent = "Não foi possível copiar automaticamente.";
      }
    }
  });

  localizeExistingTimes();
  updateFilters();

  if (typeof window.EventSource === "undefined") {
    if (streamStatus) {
      streamStatus.textContent = "Streaming indisponível neste navegador";
    }
    return;
  }
  const streamUrl = new URL(viewer.dataset.streamUrl || "/logs/stream", window.location.origin);
  if (viewer.dataset.lastCursor) {
    streamUrl.searchParams.set("cursor", viewer.dataset.lastCursor);
  }
  const eventSource = new EventSource(streamUrl);
  eventSource.addEventListener("open", () => {
    if (streamStatus) {
      streamStatus.textContent = "Streaming conectado";
      streamStatus.classList.remove("text-warning", "text-danger");
      streamStatus.classList.add("text-positive");
    }
  });
  eventSource.addEventListener("log", (event) => {
    try {
      receivePayload(JSON.parse(event.data));
    } catch {
      // Eventos inválidos são ignorados sem inserir HTML ou detalhes internos na página.
    }
  });
  eventSource.addEventListener("error", () => {
    if (streamStatus) {
      streamStatus.textContent = "Reconectando ao streaming…";
      streamStatus.classList.remove("text-positive");
      streamStatus.classList.add("text-warning");
    }
  });
  window.addEventListener("beforeunload", () => eventSource.close(), { once: true });
}

initializeLogViewer();

function initializePlayersPage(root = document) {
  root.querySelectorAll("[data-local-time]").forEach((element) => {
    const timestamp = element.getAttribute("datetime");
    if (!timestamp) {
      return;
    }
    const date = new Date(timestamp);
    if (!Number.isNaN(date.getTime())) {
      element.textContent = date.toLocaleString("pt-BR");
    }
  });

  root.querySelectorAll("[data-announcement-form]").forEach((form) => {
    const message = form.querySelector("[data-announcement-message]");
    const count = form.querySelector("[data-announcement-count]");
    const modal = root.querySelector("[data-announcement-modal]");
    const preview = modal?.querySelector("[data-announcement-preview]");
    const cancelButton = modal?.querySelector("[data-announcement-modal-cancel]");
    const confirmButton = modal?.querySelector("[data-announcement-modal-confirm]");
    const submitButton = form.querySelector('button[type="submit"]');
    if (!(message instanceof HTMLTextAreaElement)) {
      return;
    }
    const updateCount = () => {
      if (count) {
        const amount = [...message.value].length;
        count.textContent = `${amount} ${amount === 1 ? "caractere" : "caracteres"}`;
      }
    };
    message.addEventListener("input", updateCount);
    updateCount();

    if (!(modal instanceof HTMLDialogElement) || !preview || !confirmButton) {
      return;
    }

    form.addEventListener("submit", (event) => {
      if (form.dataset.modalConfirmed === "true") {
        delete form.dataset.modalConfirmed;
        return;
      }
      event.preventDefault();
      preview.textContent = message.value;
      modal.showModal();
    });

    cancelButton?.addEventListener("click", () => modal.close());
    confirmButton.addEventListener("click", () => {
      modal.close();
      form.dataset.modalConfirmed = "true";
      form.requestSubmit();
    });
    modal.addEventListener("click", (event) => {
      if (event.target === modal) {
        modal.close();
      }
    });
    modal.addEventListener("close", () => submitButton?.focus());
  });
}

initializePlayersPage();
