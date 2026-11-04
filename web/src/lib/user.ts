/** Which user's memory the UI acts as. Kept in localStorage; the demo seeds the user "demo". */
const KEY = "conductor-user";

export function getUserId(): string {
  try {
    return localStorage.getItem(KEY) || "demo";
  } catch {
    return "demo";
  }
}

export function setUserId(id: string): void {
  try {
    localStorage.setItem(KEY, id);
  } catch {
    /* not persisted */
  }
}
