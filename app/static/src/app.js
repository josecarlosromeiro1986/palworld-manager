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
