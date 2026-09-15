import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { CssBaseline, ThemeProvider, createTheme } from "@mui/material";
import App from "./App";

// Docker Desktop-inspired dark theme: Docker blue accent on a calm slate
// canvas, with a slightly lighter sidebar/surface separation and soft borders.
const theme = createTheme({
  palette: {
    mode: "dark",
    primary: { main: "#2496ed", light: "#5eb3f4", dark: "#0b6bcb", contrastText: "#ffffff" },
    secondary: { main: "#a78bfa" },
    success: { main: "#3fb950" },
    warning: { main: "#d29922" },
    error: { main: "#f85149" },
    info: { main: "#2496ed" },
    background: { default: "#0d1117", paper: "#161b22" },
    divider: "rgba(240, 246, 252, 0.10)",
    text: { primary: "#e6edf3", secondary: "#8b98a9" }
  },
  typography: {
    fontFamily: '"Segoe UI Variable", "Segoe UI", Inter, system-ui, sans-serif',
    button: { fontWeight: 600, letterSpacing: 0, textTransform: "none" },
    overline: { fontSize: "0.68rem", letterSpacing: ".1em", fontWeight: 700 },
    h4: { fontWeight: 700, letterSpacing: "-0.02em" },
    h5: { fontWeight: 700, letterSpacing: "-0.015em" },
    h6: { fontWeight: 700, letterSpacing: "-0.01em" }
  },
  shape: { borderRadius: 10 },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: { backgroundColor: "#0d1117" },
        "*": { boxSizing: "border-box" },
        "*::-webkit-scrollbar": { width: 10, height: 10 },
        "*::-webkit-scrollbar-thumb": { background: "rgba(139, 152, 169, .28)", borderRadius: 8, border: "2px solid transparent", backgroundClip: "content-box" },
        "*::-webkit-scrollbar-thumb:hover": { background: "rgba(139, 152, 169, .45)", backgroundClip: "content-box" }
      }
    },
    MuiPaper: { styleOverrides: { root: { backgroundImage: "none", border: "1px solid rgba(240, 246, 252, 0.08)" } } },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: { borderRadius: 8, minHeight: 34 },
        containedPrimary: { boxShadow: "0 1px 0 rgba(0,0,0,.25)" }
      }
    },
    MuiChip: { styleOverrides: { root: { fontWeight: 600 } } },
    MuiTextField: { defaultProps: { size: "small" } },
    MuiTooltip: { defaultProps: { arrow: true } },
    MuiTab: { styleOverrides: { root: { textTransform: "none", fontWeight: 600, minHeight: 44 } } }
  }
});
createRoot(document.getElementById("root")!).render(<StrictMode><ThemeProvider theme={theme}><CssBaseline /><App /></ThemeProvider></StrictMode>);
