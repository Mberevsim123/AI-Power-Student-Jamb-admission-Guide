import { useEffect, useRef, useState } from "react";

const reducedMotion = () =>
  typeof window !== "undefined" &&
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/**
 * Advance a reveal position to a boundary that renders cleanly: the end of a word,
 * a whole table row, and never the middle of a Markdown link.
 */
export function snap(text: string, position: number) {
  if (position >= text.length) return text.length;
  const lineStart = text.lastIndexOf("\n", position - 1) + 1;
  if (text.startsWith("|", lineStart) || /^\s*\|/.test(text.slice(lineStart, lineStart + 4))) {
    const lineEnd = text.indexOf("\n", position);
    return lineEnd === -1 ? text.length : lineEnd + 1;
  }
  let end = position;
  while (end < text.length && !/\s/.test(text[end])) end++;
  // Inside "[label](url)": show the whole link at once.
  const open = text.lastIndexOf("[", end);
  if (open >= lineStart && text.indexOf("]", open) !== -1) {
    const close = text.indexOf(")", text.indexOf("](", open));
    if (text.indexOf("](", open) !== -1 && close >= end) end = close + 1;
  }
  return Math.min(text.length, end);
}

/**
 * Progressively reveal an answer that has already been checked on the server.
 * The pace adapts to length so any answer finishes in about two to three seconds;
 * users who prefer reduced motion see it at once.
 */
export function useReveal(text: string, active: boolean) {
  const instant = !active || reducedMotion();
  const [shown, setShown] = useState(instant ? text.length : 0);
  const position = useRef(shown);
  useEffect(() => {
    if (instant) {
      position.current = text.length;
      setShown(text.length);
      return;
    }
    const perSecond = Math.min(1100, Math.max(220, text.length / 2.4));
    let frame = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const elapsed = now - last;
      if (elapsed >= 32) {
        const next = snap(text, position.current + Math.max(1, Math.round((elapsed / 1000) * perSecond)));
        position.current = next;
        last = now;
        setShown(next);
      }
      if (position.current < text.length) frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [text, instant]);
  const end = Math.min(shown, text.length);
  return { visible: text.slice(0, end), revealing: end < text.length };
}
