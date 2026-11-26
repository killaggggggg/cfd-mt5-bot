import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/anuphan/300.css";
import "@fontsource/anuphan/400.css";
import "@fontsource/anuphan/500.css";
import "@fontsource/anuphan/600.css";
import "@fontsource/anuphan/700.css";
import App from "./App";
import "./styles.css";

try {
  if (localStorage.getItem("cfd_ops_theme") === "dark") {
    document.documentElement.dataset.theme = "dark";
  }
} catch {
  // ignore storage access errors
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
