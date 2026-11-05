import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./theme.css";
import "@google/model-viewer";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
