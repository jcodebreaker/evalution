import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Root } from "./features/shell/Root";
import "./index.css";

const root = document.getElementById("root");

if (root) {
  createRoot(root).render(
    <StrictMode>
      <Root />
    </StrictMode>,
  );
}
