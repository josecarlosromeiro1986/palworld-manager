import { expect, test } from "@playwright/test";

const E2E_USERNAME = "admin-e2e";
const E2E_PASSWORD = "senha-ficticia-e2e";

async function login(page) {
  await page.goto("/login");
  await page.getByLabel("Usuário").fill(E2E_USERNAME);
  await page.getByLabel("Senha", { exact: true }).fill(E2E_PASSWORD);
  await page.getByRole("button", { name: "Entrar", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
}

async function confirmAction(page, buttonName) {
  const dialog = page.locator("[data-confirmation-modal]");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: buttonName, exact: true }).click();
  await expect(dialog).toBeHidden();
}

test("protege rota privada e conclui login e logout", async ({ page }) => {
  await page.goto("/backups");
  await expect(page).toHaveURL(/\/login(?:\?.*)?$/);
  await expect(page.getByRole("heading", { name: "Entrar" })).toBeVisible();

  await login(page);
  await page.getByRole("button", { name: "Sair", exact: true }).click();

  await expect(page).toHaveURL(/\/login$/);
  await page.goto("/");
  await expect(page).toHaveURL(/\/login(?:\?.*)?$/);
});

test("executa Stop e Restart pelos fakes do ambiente de teste", async ({ page }) => {
  await login(page);
  const controls = page.locator('section[aria-labelledby="lifecycle-title"]');

  await controls.getByLabel("Aviso antes de parar").selectOption("0");
  await controls.getByRole("button", { name: "Parar", exact: true }).click();
  await confirmAction(page, "Iniciar desligamento");

  const stopJob = page.locator("[data-shutdown-job]");
  await expect(stopJob).toHaveAttribute("data-job-status", "SUCCEEDED", { timeout: 30_000 });
  await expect(stopJob).toContainText("Estado final: OFFLINE");

  await controls.getByRole("button", { name: "Reiniciar", exact: true }).click();
  await confirmAction(page, "Reiniciar");

  const restartJob = page.locator("[data-lifecycle-job]");
  await expect(restartJob).toHaveAttribute("data-job-status", "SUCCEEDED", {
    timeout: 30_000,
  });
  await expect(restartJob).toContainText("Estado final: ONLINE");
});

test("confirma Restore local com a frase RESTAURAR", async ({ page }) => {
  await login(page);
  await page.goto("/backups");

  await page.getByRole("button", { name: "Backup agora", exact: true }).click();
  await confirmAction(page, "Criar backup");
  await expect(page.locator("[data-backup-job]")).toContainText("SUCCEEDED", {
    timeout: 30_000,
  });

  const confirmation = page.getByPlaceholder("RESTAURAR").first();
  await expect(confirmation).toBeVisible();
  await confirmation.fill("RESTAURAR");
  await confirmation
    .locator("xpath=ancestor::form")
    .getByRole("button", { name: "Restaurar", exact: true })
    .click();
  await confirmAction(page, "Iniciar Restore");

  const restoreJob = page.locator("[data-restore-job]");
  await expect(restoreJob).toContainText("Restore concluído", {
    timeout: 45_000,
  });
  await expect(restoreJob).toContainText("SUCCEEDED");
});

test("salva configuração reconhecida pelo modal compartilhado", async ({ page }) => {
  await login(page);
  await page.goto("/palworld-settings");

  const serverName = page.locator("#setting-ServerName");
  await expect(serverName).toBeVisible();
  await serverName.fill("Servidor E2E");
  await page.getByRole("button", { name: "Salvar configurações", exact: true }).click();
  await confirmAction(page, "Criar backup e salvar");

  await expect(page.getByRole("status")).toContainText("Configurações salvas com backup");
  await expect(page.getByRole("heading", { name: "Restart necessário" })).toBeVisible();
  await expect(page.locator("#setting-ServerName")).toHaveValue("Servidor E2E");
});
