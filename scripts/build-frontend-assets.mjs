import { copyFile, mkdir, rm, writeFile } from "node:fs/promises";
import { dirname } from "node:path";
import { icons } from "lucide";

const assets = [
  ["node_modules/htmx.org/dist/htmx.min.js", "app/static/dist/vendor/htmx.min.js"],
  ["node_modules/chart.js/dist/chart.umd.js", "app/static/dist/vendor/chart.umd.js"],
  ["app/static/src/app.js", "app/static/dist/app.js"],
];

const selectedIcons = {
  activity: icons.Activity,
  archive: icons.Archive,
  copy: icons.Copy,
  cpu: icons.Cpu,
  eye: icons.Eye,
  "hard-drive": icons.HardDrive,
  history: icons.History,
  "layout-dashboard": icons.LayoutDashboard,
  "log-in": icons.LogIn,
  "log-out": icons.LogOut,
  memory: icons.MemoryStick,
  menu: icons.Menu,
  "refresh-cw": icons.RefreshCcw,
  "scroll-text": icons.ScrollText,
  "server-cog": icons.ServerCog,
  settings: icons.Settings,
  sliders: icons.SlidersHorizontal,
  stethoscope: icons.Stethoscope,
  users: icons.Users,
  x: icons.X,
};

function escapeAttribute(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll('"', "&quot;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

function renderNode([tag, attributes, children = []]) {
  const serializedAttributes = Object.entries(attributes)
    .map(([name, value]) => `${name}="${escapeAttribute(value)}"`)
    .join(" ");
  const serializedChildren = children.map(renderNode).join("");
  return `<${tag} ${serializedAttributes}>${serializedChildren}</${tag}>`;
}

const symbols = Object.entries(selectedIcons)
  .map(([name, nodes]) => {
    const content = nodes.map(renderNode).join("");
    return `<symbol id="icon-${name}" viewBox="0 0 24 24">${content}</symbol>`;
  })
  .join("");

const sprite = `<svg xmlns="http://www.w3.org/2000/svg">${symbols}</svg>`;

for (const [source, destination] of assets) {
  await mkdir(dirname(destination), { recursive: true });
  await rm(destination, { force: true });
  await copyFile(source, destination);
}

await rm("app/static/dist/icons.svg", { force: true });
await writeFile("app/static/dist/icons.svg", sprite);
