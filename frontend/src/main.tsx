import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/nunito/400.css";
import "@fontsource/nunito/700.css";
import "./styles.css";
import App from "./App";
import { BRAND } from "./brand";

// The tab's title and icon come from the brand, so they change in one place with it.
document.title = `${BRAND.name} ${BRAND.product.toLowerCase()}`;
const primary = getComputedStyle(document.documentElement)
  .getPropertyValue("--brand-primary")
  .trim();
const icon = document.createElement("link");
icon.rel = "icon";
icon.href = `data:image/svg+xml,${encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16"><circle cx="8" cy="8" r="8" fill="${primary}"/></svg>`,
)}`;
document.head.append(icon);

const root = document.getElementById("root");
if (!root) throw new Error("index.html has no #root element");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
