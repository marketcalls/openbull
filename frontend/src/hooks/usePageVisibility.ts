import { useEffect, useRef, useState } from "react";

export interface UsePageVisibilityReturn {
  /** True when the document is currently visible. */
  isVisible: boolean;
  /** True for one render after the document transitions hidden → visible. */
  wasHidden: boolean;
}

/**
 * React wrapper around the Page Visibility API. Returns `isVisible` (true when
 * `document.visibilityState === "visible"`), and a one-shot `wasHidden` flag
 * useful for triggering a refetch on tab return.
 *
 * Listens for both `visibilitychange` and window focus/blur as a fallback for
 * browsers that don't fire visibilitychange on minimise.
 */
export function usePageVisibility(): UsePageVisibilityReturn {
  const [isVisible, setIsVisible] = useState<boolean>(() =>
    typeof document !== "undefined" ? document.visibilityState === "visible" : true
  );
  const [wasHidden, setWasHidden] = useState(false);
  const prevVisibleRef = useRef<boolean>(isVisible);

  useEffect(() => {
    if (typeof document === "undefined") return;

    const handle = () => {
      const nowVisible = document.visibilityState === "visible";
      if (nowVisible && !prevVisibleRef.current) {
        setWasHidden(true);
      }
      prevVisibleRef.current = nowVisible;
      setIsVisible(nowVisible);
    };

    const handleFocus = () => {
      if (document.visibilityState === "visible" && !prevVisibleRef.current) handle();
    };
    const handleBlur = () => {
      if (document.visibilityState === "hidden" && prevVisibleRef.current) handle();
    };

    document.addEventListener("visibilitychange", handle);
    window.addEventListener("focus", handleFocus);
    window.addEventListener("blur", handleBlur);
    return () => {
      document.removeEventListener("visibilitychange", handle);
      window.removeEventListener("focus", handleFocus);
      window.removeEventListener("blur", handleBlur);
    };
  }, []);

  // Reset `wasHidden` after a render so consumers see it once.
  useEffect(() => {
    if (!wasHidden) return;
    const t = setTimeout(() => setWasHidden(false), 100);
    return () => clearTimeout(t);
  }, [wasHidden]);

  return { isVisible, wasHidden };
}
