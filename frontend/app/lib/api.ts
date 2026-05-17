/**
 * Resolve the backend API base URL at runtime based on window.location.
 * This keeps a single build working on any host without build-time env vars.
 *
 * Convention: backend runs on the same hostname as the frontend, port 8001.
 */
export function getApiBaseUrl(): string {
  if (typeof window !== 'undefined') {
    const { protocol, hostname } = window.location
    return `${protocol}//${hostname}:8001`
  }
  return 'http://localhost:8001'
}
