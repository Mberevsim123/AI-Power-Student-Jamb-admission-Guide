/** "X University, Abeokuta, Ogun State" → "X University, Abeokuta" when the state is shown below. */
export function displayName(name: string, state: string) {
  const escaped = state.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const trimmed = name.replace(new RegExp(`[\\s,–-]+${escaped}(\\s+State)?\\.?$`, "i"), "");
  return trimmed.length >= 6 ? trimmed : name;
}
