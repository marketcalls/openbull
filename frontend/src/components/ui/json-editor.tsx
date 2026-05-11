/**
 * CodeMirror 6 JSON editor — same colour scheme as the response-pane
 * regex tokenizer so the request and response sides look like a matched pair.
 *
 * Ported from openalgo/frontend/src/components/ui/json-editor.tsx. The
 * dark/light decision is sourced from openbull's ThemeContext + the
 * sandbox-active flag (sandbox always renders dark to mirror the topbar tint).
 */
import { useMemo } from "react";
import CodeMirror from "@uiw/react-codemirror";
import { json } from "@codemirror/lang-json";
import { EditorView } from "@codemirror/view";
import type { Extension } from "@codemirror/state";
import { tags as t } from "@lezer/highlight";
import { createTheme } from "@uiw/codemirror-themes";

import { useTheme } from "@/contexts/ThemeContext";
import { useTradingMode } from "@/contexts/TradingModeContext";

interface JsonEditorProps {
  value: string;
  onChange?: (value: string) => void;
  readOnly?: boolean;
  placeholder?: string;
  className?: string;
  lineWrapping?: boolean;
}

// Keys: sky-400, Strings: emerald-400, Numbers: orange-400, Booleans: purple-400, Null: red-400
const createJsonTheme = (isDark: boolean): Extension =>
  createTheme({
    theme: isDark ? "dark" : "light",
    settings: {
      background: "transparent",
      foreground: isDark ? "#e5e5e5" : "#171717",
      caret: isDark ? "#38bdf8" : "#0284c7",
      selection: isDark ? "rgba(56, 189, 248, 0.2)" : "rgba(2, 132, 199, 0.2)",
      selectionMatch: isDark ? "rgba(56, 189, 248, 0.1)" : "rgba(2, 132, 199, 0.1)",
      lineHighlight: "transparent",
      gutterBackground: "transparent",
      gutterForeground: isDark ? "rgba(255, 255, 255, 0.3)" : "rgba(0, 0, 0, 0.3)",
      gutterBorder: "transparent",
    },
    styles: [
      { tag: t.propertyName, color: "#38bdf8" },
      { tag: t.string, color: "#34d399" },
      { tag: t.number, color: "#fb923c" },
      { tag: t.bool, color: "#c084fc" },
      { tag: t.null, color: "#f87171" },
      { tag: t.bracket, color: isDark ? "#a3a3a3" : "#525252" },
      { tag: t.punctuation, color: isDark ? "#a3a3a3" : "#525252" },
    ],
  });

const createBaseTheme = (isDark: boolean): Extension => {
  const borderColor = isDark ? "rgba(255, 255, 255, 0.1)" : "rgba(0, 0, 0, 0.1)";
  const gutterBg = isDark ? "rgba(255, 255, 255, 0.025)" : "rgba(0, 0, 0, 0.02)";

  return EditorView.theme({
    "&": {
      fontSize: "12px",
      fontFamily:
        'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
      height: "100%",
      backgroundColor: "transparent",
    },
    "&.cm-editor": { height: "100%", backgroundColor: "transparent" },
    ".cm-scroller": { overflow: "auto", height: "100%", backgroundColor: "transparent" },
    ".cm-content": { padding: "12px 0", lineHeight: "20px", backgroundColor: "transparent" },
    ".cm-line": { padding: "0 12px" },
    ".cm-gutters": {
      backgroundColor: gutterBg,
      borderRight: `1px solid ${borderColor}`,
      paddingRight: "4px",
    },
    ".cm-gutter": { minWidth: "40px" },
    ".cm-gutterElement": { padding: "0 8px 0 12px", lineHeight: "20px" },
    ".cm-placeholder": { color: "rgba(128, 128, 128, 0.5)" },
    "&.cm-focused": { outline: "none" },
    ".cm-activeLine": { backgroundColor: "transparent" },
    ".cm-activeLineGutter": { backgroundColor: "transparent" },
  });
};

export function JsonEditor({
  value,
  onChange,
  readOnly = false,
  placeholder,
  className = "",
  lineWrapping = true,
}: JsonEditorProps) {
  const { theme } = useTheme();
  const { isSandbox } = useTradingMode();
  // Sandbox always renders dark (matches the topbar amber/dark tint).
  const isDark = theme === "dark" || isSandbox;

  const extensions = useMemo(() => {
    const exts = [json(), createJsonTheme(isDark), createBaseTheme(isDark)];
    if (lineWrapping) exts.push(EditorView.lineWrapping);
    return exts;
  }, [isDark, lineWrapping]);

  return (
    <div className={`h-full w-full ${className}`}>
      <CodeMirror
        value={value}
        onChange={onChange}
        extensions={extensions}
        readOnly={readOnly}
        placeholder={placeholder}
        height="100%"
        theme={isDark ? "dark" : "light"}
        basicSetup={{
          lineNumbers: true,
          highlightActiveLineGutter: false,
          highlightActiveLine: false,
          foldGutter: false,
          dropCursor: true,
          allowMultipleSelections: false,
          indentOnInput: true,
          bracketMatching: true,
          closeBrackets: true,
          autocompletion: false,
          rectangularSelection: false,
          crosshairCursor: false,
          highlightSelectionMatches: false,
          searchKeymap: false,
          tabSize: 2,
        }}
      />
    </div>
  );
}
