import { useEffect, useState } from "react";
import { Type } from "lucide-react";
import { Modal } from "./ui";

type TextSize = "standard" | "large" | "extra-large";
interface Appearance {
  size: TextSize;
  bold: boolean;
}
const storageKey = "jamb-guide.appearance.v1";

function initialAppearance(): Appearance {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || "null");
    if (
      saved &&
      ["standard", "large", "extra-large"].includes(saved.size) &&
      typeof saved.bold === "boolean"
    )
      return saved;
  } catch {
    // Appearance controls also work when browser storage is unavailable.
  }
  return { size: "standard", bold: false };
}

export function AppearanceControl() {
  const [open, setOpen] = useState(false);
  const [appearance, setAppearance] = useState(initialAppearance);

  useEffect(() => {
    document.documentElement.dataset.textSize = appearance.size;
    document.documentElement.dataset.boldText = String(appearance.bold);
    try {
      localStorage.setItem(storageKey, JSON.stringify(appearance));
    } catch {
      // Keep the current preference in memory for this visit.
    }
  }, [appearance]);

  return (
    <>
      <button
        className="icon-button appearance-button"
        aria-label="Text appearance"
        title="Text size and bold text"
        aria-haspopup="dialog"
        onClick={() => setOpen(true)}
      >
        <Type size={20} />
      </button>
      {open && (
        <Modal title="Text appearance" onClose={() => setOpen(false)}>
          <div className="modal-content appearance-settings">
            <p>
              Choose the text size and weight that feel comfortable to read.
            </p>
            <fieldset className="text-size-options">
              <legend>Text size</legend>
              <div>
                {(
                  [
                    ["standard", "Standard"],
                    ["large", "Large"],
                    ["extra-large", "Extra large"],
                  ] as const
                ).map(([size, label]) => (
                  <label className="text-size-option" key={size}>
                    <input
                      type="radio"
                      name="text-size"
                      value={size}
                      checked={appearance.size === size}
                      onChange={() =>
                        setAppearance((old) => ({ ...old, size }))
                      }
                    />
                    <span>{label}</span>
                  </label>
                ))}
              </div>
            </fieldset>
            <label className="appearance-bold">
              <span>
                Bold text
                <small>Give body text and controls more weight.</small>
              </span>
              <input
                type="checkbox"
                aria-label="Bold text"
                checked={appearance.bold}
                onChange={(event) =>
                  setAppearance((old) => ({
                    ...old,
                    bold: event.target.checked,
                  }))
                }
              />
            </label>
            <div className="appearance-preview">
              <strong>Your next step starts here.</strong>
              <p>Ask a question. Explore your options. Plan with confidence.</p>
            </div>
            <button
              className="button primary full"
              onClick={() => setOpen(false)}
            >
              Done
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}
