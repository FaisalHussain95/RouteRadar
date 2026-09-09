import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

// Self-hosted rather than linked from fonts.googleapis.com as the design does: the deployed
// page makes no runtime request at all, for data or for anything else, and the woff2 files
// come out of the build content-hashed like every other asset.
import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";

import "./tokens.css";
import "./styles.css";

import { App } from "./App";
import { dashboardData } from "./data";

const root = document.getElementById("root");
if (root === null) throw new Error("#root is missing from index.html");
createRoot(root).render(
  <StrictMode>
    <App data={dashboardData} />
  </StrictMode>,
);
