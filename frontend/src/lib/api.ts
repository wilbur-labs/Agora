/** Resolve the API origin for the static development console. */
export function getApiBase(): string {
  if (typeof window === "undefined") return "";
  if (window.location.port === "3000") {
    return `${window.location.protocol}//${window.location.hostname}:8000`;
  }
  return "";
}
