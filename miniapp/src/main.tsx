import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

const wa = window.Telegram?.WebApp;
wa?.ready();
wa?.expand();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
