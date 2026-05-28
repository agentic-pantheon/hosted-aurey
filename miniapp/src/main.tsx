import React from "react";
import ReactDOM from "react-dom/client";
import { applyAppTheme } from "./theme";
import App from "./App";
import "./styles.css";

const wa = window.Telegram?.WebApp;
wa?.ready();
wa?.expand();
applyAppTheme();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
